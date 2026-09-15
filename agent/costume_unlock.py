"""MaaFramework custom action for per-character default 3D costume targets."""
import json
import re
import time
import unicodedata
from pathlib import Path

import numpy as np
from maa.custom_action import CustomAction

from costume_policy import ROSTER, parse_ratio, parse_target, purchase_decision, scope_members


class FlowError(RuntimeError):
    pass


def normalized(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def scroll_displacement(previous, current):
    """Estimate vertical content movement from overlapping stationary grid pixels."""
    old = previous[:, ::4].astype(np.float32)
    new = current[:, ::4].astype(np.float32)
    scores = [(float(np.mean(np.abs(old[delta:]-new[:len(new)-delta]))),delta) for delta in range(211)]
    score, delta = min(scores)
    if score > 8:
        raise FlowError(f"服装列表滚动重叠无法可靠匹配：{score:.2f}")
    return delta


class CostumeFlow:
    def __init__(self, context, target, *, inspect_only=False, roster=None, scope="all", selection=""):
        self.ctx = context
        self.controller = context.tasker.controller
        self.target = parse_target(target)
        if not isinstance(inspect_only, bool):
            raise ValueError("inspect_only 必须为布尔值")
        self.inspect_only = inspect_only
        self.roster = ROSTER if roster is None else roster
        self.selected_members = None if scope == "all" else scope_members(scope, selection)
        self.report = {"target": self.target, "inspect_only": inspect_only, "characters": []}
        self.report["purchases"] = []
        self.report["scope"] = {"kind": scope, "value": selection}
        self.image = None
        self.active_band = None
        self.active_index = None
        self.count_is_lower_bound = False
        self.costume_character = None
        self.seen_costume_names = set()

    def check_stop(self):
        if self.ctx.tasker.stopping:
            raise FlowError("任务已被用户停止")

    def snap(self):
        self.check_stop()
        if not self.controller.post_screencap().wait().succeeded:
            raise FlowError("截图失败")
        self.image = self.controller.cached_image
        if self.image.shape[:2] != (720, 1280):
            raise FlowError("当前任务要求横屏 1280×720 的框架截图")
        return self.image

    def reco(self, name, **override):
        self.check_stop()
        result = self.ctx.run_recognition(name, self.image, {name: override} if override else None)
        return result if result and result.hit else None

    def ocr(self, roi, expected=".+", only_rec=False):
        result = self.reco("CU_OCR", roi=roi, expected=expected, only_rec=only_rec)
        return list(result.filtered_results) if result else []

    def text(self, roi):
        return " ".join(item.text for item in self.ocr(roi))

    def wait(self, node, timeout=12):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.snap()
            hit = self.reco(node)
            if hit:
                return hit
            time.sleep(0.25)
        raise FlowError(f"页面识别超时：{node}")

    def tap(self, x, y):
        self.check_stop()
        if not self.controller.post_click(int(x), int(y)).wait().succeeded:
            raise FlowError("点击失败")
        time.sleep(0.9)

    def click(self, node):
        hit = self.wait(node)
        x, y, w, h = hit.box
        self.tap(x + w // 2, y + h // 2)

    def swipe(self, x, y1, y2):
        self.check_stop()
        if not self.controller.post_swipe(x, y1, x, y2, 650).wait().succeeded:
            raise FlowError("滑动失败")
        time.sleep(0.5)

    def back(self):
        self.click("CU_Back")

    def rating_select(self):
        for _ in range(12):
            self.snap()
            if self.reco("CU_RatingHeader") and self.reco("CU_SelectCharacter"):
                return
            if self.reco("CU_LeavePreview"):
                self.click("CU_LeaveConfirm")
            elif self.reco("CU_UnlockSuccess"):
                self.click("CU_KeepCostume")
            elif self.reco("CU_TaskDetail"):
                self.tap(510, 600)
                self.wait("CU_CollectionHeader")
            elif self.reco("CU_RatingEntry"):
                self.click("CU_RatingEntry")
                self.wait("CU_SelectCharacter")
            elif self.reco("CU_HomeBand"):
                self.click("CU_HomeBand")
                self.wait("CU_RatingEntry")
            elif self.reco("CU_CollectionHeader"):
                self.back()
                self.wait("CU_Collection")
            elif self.reco("CU_RatingHeader"):
                self.back()
                self.wait("CU_SelectCharacter")
            elif self.reco("CU_CostumeList"):
                self.back()
                self.wait("CU_RatingEntry")
            elif self.reco("CU_Tab3D"):
                self.back()
            else:
                raise FlowError("请从游戏主页、乐队、角色评级或演出服装页面启动")
        raise FlowError("无法返回角色评级选择页面")

    def select_band(self, band, list_node="CU_SelectCharacter"):
        node = "CU_Band_" + band
        bands = [name for name, _ in ROSTER]
        target = bands.index(band)
        direction = (570, 350)
        blocked = set()
        for _ in range(12):
            self.wait(list_node)
            hit = self.reco(node)
            if hit:
                if self.reco(node, roi=[25,170,265,100]) and self.reco("CU_SelectedBandFrame"):
                    return
                x, y, w, h = hit.box
                self.tap(x+w//2, y+h//2)
                deadline = time.monotonic()+4
                while time.monotonic() < deadline:
                    self.snap()
                    if self.reco(node, roi=[25,170,265,100]) and self.reco("CU_SelectedBandFrame"):
                        return
                    time.sleep(0.15)
                raise FlowError(f"未确认选中乐队 {band}")
            visible = [i for i, name in enumerate(bands) if self.reco("CU_Band_"+name)]
            if visible:
                direction = (350,570) if target < min(visible) else (570,350)
            if direction in blocked:
                direction = direction[::-1]
            if direction in blocked:
                break
            before = self.image[165:680,20:294].astype(float)
            self.swipe(155, *direction)
            self.snap()
            after = self.image[165:680,20:294].astype(float)
            if np.mean(np.abs(before-after)) < 1:
                blocked.add(direction)
        raise FlowError(f"找不到乐队 {band}")

    def verify_character(self, character):
        self.wait("CU_RatingHeader")
        value = normalized(self.text([115, 50, 285, 40]))
        expected = normalized(character)
        if expected not in value:
            raise FlowError(f"角色身份不匹配：期望 {character}，识别到 {value}")

    def read_count(self, character):
        self.count_is_lower_bound = False
        self.verify_character(character)
        self.click("CU_TasksTab")
        self.click("CU_Collection")
        self.wait("CU_CollectionHeader")
        previous = None
        for _ in range(500):
            self.snap()
            if self.ocr([790, 440, 340, 75], "已完成所有任务"):
                return None
            # The game can wrap between "3" and "D"; verify the full type in the detail modal.
            labels = self.ocr([750, 245, 455, 410], "演出服装")
            for label in labels:
                details = self.ocr([1060, max(245, label.box[1]-25), 140, 105], "详细")
                if details:
                    x, y, w, h = details[0].box
                    self.tap(x+w//2, y+h//2)
                    self.wait("CU_TaskDetail")
                    title = normalized(self.text([381, 137, 649, 63]))
                    if "默认3D演出服装" not in title or normalized(character) not in title:
                        raise FlowError(f"任务详情与角色或默认3D服装不符：{title}")
                    count = self.stable_ratio([389, 210, 622, 30])[0]
                    return count
                claims = self.ocr([1060, max(245, label.box[1]-25), 140, 105], "^领取$")
                if claims:
                    ratios = self.ocr([765, label.box[1]+20, 295, 80], r"\d+\s*/\s*\d+")
                    if len(ratios) != 1:
                        raise FlowError("已达成收集任务的进度无法识别")
                    lower_bound, milestone = parse_ratio(ratios[0].text)
                    if lower_bound < milestone:
                        raise FlowError("领取状态与进度不一致")
                    x,y,w,h = claims[0].box
                    self.tap(x+w//2,y+h//2)
                    self.click("CU_RewardConfirm")
                    self.wait("CU_CollectionHeader")
                    previous = None
                    break
            else:
                area = self.image[248:654, 630:1208].copy()
                if previous is not None and np.mean(np.abs(area.astype(float)-previous)) < 1:
                    break
                previous = area.astype(float)
                self.swipe(1000, 607, 315)
                continue
            continue
        raise FlowError(f"{character}：未找到默认3D服装进度，不能推断数量")

    def goto_costumes(self, character):
        self.snap()
        if self.reco("CU_TaskDetail"):
            self.click("CU_Goto3D")
        else:
            self.rating_select()
            self.back()
            self.click("CU_CostumeEntry")
            self.wait("CU_CostumeList")
            self.select_band(self.active_band, "CU_CostumeList")
            self.snap()
            x = [460,622,783,945,1106][self.active_index]
            label = normalized(self.text([x-74, 532, 148, 46]))
            if normalized(character) not in label:
                raise FlowError(f"服装角色列表身份不匹配：{character} / {label}")
            self.tap(x,310)
        self.wait("CU_Tab3D")
        if not self.reco("CU_ClothingTab"):
            self.tap(795,193)
        self.wait("CU_ClothingTab")

    def reopen_rating_character(self, character):
        self.rating_select()
        self.select_band(self.active_band)
        self.tap([460,622,783,945,1106][self.active_index],310)
        self.verify_character(character)

    def count_owned_costumes(self, character):
        self.goto_costumes(character)
        clothes = self.count_owned_grid(self.target)
        hair = 0
        if clothes < self.target:
            self.tap(1090,193)
            self.wait("CU_HairTab")
            hair = self.count_owned_grid(self.target-clothes)
        count = clothes + hair
        self.count_is_lower_bound = count >= self.target
        self.return_from_costumes(character, allow_list=True)
        self.reopen_rating_character(character)
        print(f"[服装解锁] {character}：服装 {clothes} + 发型/饰品 {hair} = {count}",flush=True)
        return count

    def count_owned_grid(self, limit):
        self.reset_costume_scroll()
        total = set()
        offset = 0
        previous = None
        for _ in range(300):
            self.snap()
            area = self.image[226:511,650:1223].copy()
            if previous is not None:
                delta = scroll_displacement(previous, area)
                if delta == 0:
                    break
                offset += delta
            badges = self.reco("CU_OwnedBadge")
            for result in badges.filtered_results if badges else []:
                x, y, _, _ = result.box
                column = min(range(4), key=lambda col: abs(x-[650,796,942,1088][col]))
                if abs(x-[650,796,942,1088][column]) > 4:
                    continue
                pos = y + offset
                if not any(col==column and abs(pos-old_y)<16 for col,old_y in total):
                    total.add((column,pos))
            if len(total) >= limit:
                break
            previous = area
            self.swipe(960,445,345)
        else:
            raise FlowError("服装列表计数超出滚动上限")
        return len(total)

    def reset_costume_scroll(self):
        """The game can retain the previous character's list position."""
        previous = None
        for _ in range(300):
            self.snap()
            area = self.image[226:511,650:1223].astype(float)
            if previous is not None and np.mean(np.abs(area-previous)) < 1:
                return
            previous = area
            self.swipe(960, 280, 480)
        raise FlowError("无法确认服装列表顶部")

    def stable_ratio(self, roi):
        values = []
        for _ in range(3):
            self.snap()
            text = self.text(roi)
            try:
                value = parse_ratio(text)
            except ValueError:
                value = None
            if value is not None and values and values[-1] == value:
                return value
            values.append(value)
            time.sleep(0.2)
        raise FlowError(f"数量读取不稳定：{text!r}")

    def return_from_costumes(self, character, allow_list=False):
        self.back()
        for _ in range(20):
            self.snap()
            if self.reco("CU_LeavePreview"):
                self.click("CU_LeaveConfirm")
            elif self.reco("CU_RatingHeader"):
                self.verify_character(character)
                self.costume_character = None
                return
            elif self.reco("CU_CostumeList"):
                if not allow_list:
                    self.reopen_rating_character(character)
                self.costume_character = None
                return
            time.sleep(0.25)
        raise FlowError("无法从服装页返回角色评级")

    def unlock_one(self, character, current):
        if self.costume_character != character:
            self.goto_costumes(character)
            self.reset_costume_scroll()
            self.costume_character = character
            self.seen_costume_names = set()
        previous = None
        seen_names = self.seen_costume_names
        for _ in range(100):
            self.snap()
            locks = self.reco("CU_Lock")
            positions = sorted((r.box for r in locks.filtered_results), key=lambda b: (b[1]//60, b[0])) if locks else []
            for x,y,w,h in positions:
                self.tap(x+w//2, y+h//2)
                self.wait("CU_UnlockButton")
                self.snap()
                color = normalized(self.text([755, 607, 151, 42]))
                if color != "默认配色":
                    raise FlowError(f"选中服装不是默认配色：{color}")
                name = self.text([756, 539, 435, 43])
                if not name:
                    raise FlowError("服装名称无法识别")
                if name in seen_names:
                    continue
                seen_names.add(name)
                self.click("CU_UnlockButton")
                self.wait("CU_UnlockConfirmTitle")
                if not self.reco("CU_TailoringKit"):
                    # Event/shop/card-only outfits must never be purchased with another currency.
                    self.click("CU_CancelUnlock")
                    continue
                kits = self.stable_ratio([575, 459, 110, 28])
                coins = self.stable_ratio([716, 527, 207, 35])
                self.snap()
                if not self.reco("CU_UnlockConfirmTitle") or not self.reco("CU_TailoringKit"):
                    raise FlowError("解锁确认页发生变化")
                decision = purchase_decision(current, self.target, kits, coins)
                if decision != "unlock" or self.inspect_only:
                    self.click("CU_CancelUnlock")
                    self.return_from_costumes(character)
                    return "inspected" if self.inspect_only else decision
                self.click("CU_ConfirmUnlock")
                purchase = {"character": character, "costume": name, "kits_before": kits[0], "kits_cost": kits[1],
                            "coins_before": coins[0], "coins_cost": coins[1], "status": "submitted"}
                self.report["purchases"].append(purchase)
                # Never retry the spending click. A delayed/unknown result is an error.
                for _ in range(40):
                    self.snap()
                    if self.reco("CU_UnlockSuccess"):
                        self.click("CU_KeepCostume")
                        self.wait("CU_Tab3D")
                        break
                    time.sleep(0.25)
                else:
                    raise FlowError("解锁结果未知，停止以避免重复消耗")
                updated = current + 1
                purchase.update(status="success_confirmed", before=current, after=updated)
                print(f"[服装解锁] {character}：已解锁，剩余 {max(0,self.target-updated)} 件",flush=True)
                return updated
            self.snap()
            area = self.image[224:512, 648:1226].copy()
            if previous is not None and np.mean(np.abs(area.astype(float)-previous)) < 1:
                break
            previous = area.astype(float)
            self.swipe(960, 482, 280)
        self.return_from_costumes(character)
        return "no_unlockable_costumes"

    def run(self):
        if self.target == 0:
            self.report["status"] = "target_reached"
            return
        self.rating_select()
        for band, members in self.roster:
            if self.selected_members is not None and not self.selected_members.intersection(members):
                continue
            self.active_band = band
            self.select_band(band)
            for index, character in enumerate(members):
                if self.selected_members is not None and character not in self.selected_members:
                    continue
                self.active_index = index
                self.wait("CU_SelectCharacter")
                self.tap([460, 622, 783, 945, 1106][index], 310)
                count = self.read_count(character)
                if count is None:
                    self.report["characters"].append({"band": band, "character": character,
                        "before": None, "after": None, "status": "collection_completed"})
                    print(f"[服装解锁] {character}：收集任务全部完成，直接跳过",flush=True)
                    self.rating_select()
                    self.select_band(band)
                    continue
                row = {"band": band, "character": character, "before": count, "after": count,
                       "count_is_lower_bound": self.count_is_lower_bound}
                self.report["characters"].append(row)
                print(f"[服装解锁] {character}: {count}/{self.target}", flush=True)
                while count < self.target:
                    result = self.unlock_one(character, count)
                    if isinstance(result, int):
                        count = result
                        row["after"] = count
                        row["after_basis"] = "initial_progress_plus_success_dialogs"
                    else:
                        row["status"] = result
                        if result == "collection_completed":
                            row["after"] = None
                        if result.startswith("insufficient_"):
                            self.report["status"] = result
                            return
                        break
                else:
                    row["status"] = "target_reached"
                if self.costume_character == character:
                    self.return_from_costumes(character)
                self.rating_select()
                self.select_band(band)
        self.report["status"] = "finished"


class UnlockDefault3DCostumes(CustomAction):
    def run(self, context, argv):
        flow = None
        output = Path("debug/costume_unlock")
        output.mkdir(parents=True, exist_ok=True)
        try:
            params = json.loads(argv.custom_action_param or "{}")
            options = context.get_node_data("CU_ExecutionOptions")
            if not options or "inspect_only" not in options.get("attach", {}):
                raise FlowError("缺少任务执行模式配置")
            scope = context.get_node_data("CU_ScopeMode")["attach"]["kind"]
            selection = ""
            if scope == "band":
                selection = context.get_node_data("CU_SelectedBand")["attach"]["value"]
            elif scope == "member":
                selection = context.get_node_data("CU_SelectedMember")["attach"]["value"]
            flow = CostumeFlow(context, params.get("target", 0), inspect_only=options["attach"]["inspect_only"],
                               scope=scope, selection=selection)
            flow.run()
            return True
        except Exception as exc:
            print(f"[服装解锁] 停止：{exc}", flush=True)
            if flow:
                flow.report.update(status="error", error=str(exc))
            return False
        finally:
            if flow:
                stamp = time.strftime("%Y%m%d-%H%M%S")
                if flow.report.get("status") == "error" and flow.image is not None:
                    from PIL import Image
                    Image.fromarray(flow.image[:, :, ::-1]).save(output / f"error-{stamp}.png")
                (output / f"report-{stamp}.json").write_text(json.dumps(flow.report, ensure_ascii=False, indent=2), encoding="utf-8")
