"""Home rewards, Michelle collections and the daily free recruitment."""
import json
from pathlib import Path
import time

import numpy as np
from maa.custom_action import CustomAction

from costume_unlock import CostumeFlow, FlowError, normalized
from notifications import NotificationMixin
from daily_policy import (EXCHANGE_CATEGORIES, MISSION_CATEGORIES, integer,
                          remaining_draws, verify_exchange, verify_free_confirmation,
                          selected_exchange_categories)


class DailyFlow(NotificationMixin, CostumeFlow):
    def __init__(self, context):
        super().__init__(context, 0)
        self.report = {"status": "running", "claims": [], "exchanges": [], "draws": 0}

    def hit_text(self, roi, expected):
        hits = self.ocr(roi, expected)
        return hits[0] if hits else None

    def tap_hit(self, hit):
        x, y, w, h = hit.box
        self.tap(x+w//2, y+h//2)

    def stable_integer(self, roi):
        values = []
        for _ in range(2):
            self.snap()
            values.append(integer(self.text(roi)))
            time.sleep(0.15)
        if values[0] != values[1]:
            raise FlowError("数字读数不稳定")
        return values[0]

    def home(self):
        for _ in range(6):
            self.snap()
            if self.dismiss_notifications():
                continue
            if self.reco("DY_ObtainedHeader") or self.reco("DY_ClaimedHeader"):
                self.tap(640,601)
            elif self.reco("DY_ExchangeSuccess"):
                self.tap(640,526)
            elif self.reco("DY_ExchangeQuantity") or self.reco("DY_ExchangeVerify"):
                self.tap(509,582)
            elif self.reco("DY_FreeConfirm"):
                self.tap(509,476)
            else:
                self.require_clear_notification_overlay()
                break
        if self.reco("CU_HomeBand"):
            return
        if not self.reco("DY_MenuHeader"):
            self.click("DY_MenuButton")
        self.wait("DY_MenuHeader")
        self.click("DY_MenuHome")
        self.wait("CU_HomeBand", 25)

    def gifts(self):
        self.home()
        self.click("DY_GiftEntry")
        for _ in range(100):
            self.wait("DY_GiftHeader")
            if self.reco("DY_GiftEmpty"):
                self.report["status"] = "finished"
                self.home()
                return
            button = self.hit_text([975,105,225,63], "一键领取")
            if not button:
                raise FlowError("礼物列表中找不到一键领取")
            self.tap_hit(button)
            self.wait("DY_ClaimedHeader")
            if not self.dismiss_notifications():
                self.tap(640, 601)
            self.report["claims"].append("gifts")
        raise FlowError("礼物领取未收敛，请检查容量限制")

    def select_mission_category(self, category):
        print(f"[任务奖励] 检查：{category}",flush=True)
        for direction in ((560,320), (310,570)):
            previous = None
            for _ in range(12):
                self.wait("DY_MissionHeader")
                label = self.hit_text([25,102,254,600], category)
                if label:
                    self.tap_hit(label)
                    self.wait("DY_MissionHeader")
                    selected = normalized(self.text([45,178,215,72]))
                    if normalized(category) not in selected:
                        raise FlowError(f"未确认任务分类：{category} / {selected}")
                    return
                area = self.image[100:710,25:281].copy()
                if previous is not None and np.mean(np.abs(area.astype(float)-previous)) < 1:
                    break
                previous = area.astype(float)
                self.swipe(150,*direction)
        raise FlowError(f"找不到任务分类：{category}")

    def claim_mission_category(self, category):
        # The all-claim button covers the category, including its offscreen rows/pages.
        for _ in range(100):
            self.wait("DY_MissionHeader")
            button = self.hit_text([1020,100,252,96], "全部领取|一键领取")
            if not button:
                if category == "邀请邦友" and self.hit_text([950,580,282,76], "^创建邀请码$|^输入邀请码$"):
                    self.report.setdefault('skipped_missions',[]).append({'category':category,'reason':'invitation_not_linked'})
                    return  # No existing invitation relationship, hence no rewards to claim.
                if self.hit_text([600,365,350,65], '^此任务已被锁定$'):
                    self.report.setdefault('skipped_missions',[]).append({'category':category,'reason':'locked'})
                    return
                raise FlowError(f"任务分类没有已支持的领取按钮：{category}")
            x,y,w,h = button.box
            # Sample the button fill beside its text: disabled buttons are grey.
            enabled = np.median(self.image[y+2:y+h-2, max(1020,x-25):x-7]) > 220
            if not enabled:
                return
            self.tap_hit(button)
            self.wait("DY_ClaimedHeader")
            if not self.dismiss_notifications():
                self.tap(640,601)
            self.report["claims"].append(category)
        raise FlowError(f"任务奖励领取未收敛：{category}")

    def missions(self):
        self.home()
        self.click("DY_MissionEntry")
        for category in MISSION_CATEGORIES:
            self.select_mission_category(category)
            self.claim_mission_category(category)
        self.report["status"] = "finished"
        self.home()

    def open_exchange(self):
        self.home()
        self.click("DY_MenuButton")
        self.click("DY_MenuExchange")
        self.wait("DY_ExchangeHeader")
        self.click("DY_MichelleEntry")
        self.wait("DY_MichelleHeader")

    def select_exchange_category(self, category):
        print(f"[贴纸交换] 检查：{category}",flush=True)
        self.wait("DY_MichelleHeader")
        x = dict(zip(EXCHANGE_CATEGORIES, (574,704,834,964,1094)))[category]
        self.tap(x,190)
        self.wait("DY_MichelleHeader")
        if np.median(self.image[177:205,x-46:x-27]) < 225:
            raise FlowError(f"未确认交换分类：{category}")
        return x

    def exchange_item(self, category, hit):
        cx = min((277,519,762,1004), key=lambda x: abs(x-(hit.box[0]+hit.box[2]//2)))
        cy = hit.box[1]+hit.box[3]//2
        cost = self.stable_integer([cx+5,cy-65,69,33])
        balance = self.stable_integer([390,112,76,34])
        if balance < cost:
            return False
        self.tap_hit(hit)
        self.wait("DY_ExchangeQuantity")
        if not self.reco("DY_StickerConfirm"):
            raise FlowError("未识别到米歇尔贴纸费用图标")
        name = normalized(self.text([391,301,493,37]))
        quantity = self.stable_integer([608,355,57,47])
        before = self.stable_integer([609,439,80,40])
        after = self.stable_integer([789,439,86,40])
        verify_exchange(category,quantity,before,after,cost)
        if before != balance or not name:
            raise FlowError("交换项目或余额发生变化")
        self.tap(770,582)
        self.wait("DY_ExchangeVerify")
        if normalized(self.text([391,301,493,37])) != name or not self.reco("DY_StickerConfirm"):
            raise FlowError("二次确认页的项目或货币不一致")
        quantity2 = self.stable_integer([836,368,47,35])
        before2 = self.stable_integer([609,408,80,36])
        after2 = self.stable_integer([789,408,86,36])
        verify_exchange(category,quantity2,before2,after2,cost)
        if (before2,after2) != (before,after):
            raise FlowError("二次确认页余额不一致")
        row = {"category":category,"name":name,"cost":cost,"before":before,"after":after,"status":"submitted"}
        self.report["exchanges"].append(row)
        self.tap(770,582)  # Submit exactly once. An uncertain result is never retried.
        self.wait("DY_ExchangeSuccess",20)
        self.tap(640,526)
        self.wait("DY_MichelleHeader")
        if self.stable_integer([390,112,76,34]) != after:
            raise FlowError("成功弹窗后贴纸余额未与预期一致")
        row["status"] = "success_confirmed"
        print(f"[贴纸交换] {category}：{name}，消耗 {cost} 贴纸",flush=True)
        return True

    def exchange(self, categories=None):
        if categories is None:
            categories = selected_exchange_categories(self.ctx)
        if any(category not in EXCHANGE_CATEGORIES for category in categories):
            raise ValueError("未知交换分类")
        categories = [category for category in EXCHANGE_CATEGORIES if category in categories]
        self.report["categories"] = categories
        if not categories:
            self.report["status"] = "no_categories_selected"
            self.home()
            return
        self.open_exchange()
        for category in categories:
            self.select_exchange_category(category)
            for _ in range(1500):
                self.wait("DY_MichelleHeader")
                hits = [h for h in self.ocr([156,244,965,409], "^交换$")
                        if h.box[1]+h.box[3]//2 >= 312]
                if hits:
                    hit = min(hits,key=lambda h:(h.box[1],h.box[0]))
                    if not self.exchange_item(category,hit):
                        self.report["status"] = "insufficient_stickers"
                        self.home()
                        return
                    continue
                # Exchangeable items sort first; an empty top page finishes this category.
                break
            else:
                raise FlowError(f"交换操作未收敛：{category}")
        self.report["status"] = "finished"
        self.home()

    def select_free_recruit(self):
        self.home()
        self.click("DY_RecruitEntry")
        previous = None
        for _ in range(30):
            self.snap()
            if self.reco("DY_FreeTab"):
                self.click("DY_FreeTab")
                self.wait("DY_FreeBanner")
                return
            area = self.image[100:709,28:229].astype(float)
            if previous is not None and np.mean(np.abs(area-previous)) < 1:
                break
            previous = area
            self.swipe(130,595,335)
        raise FlowError("招募列表未找到每日三次免费演出招募")

    def free_remaining(self):
        self.wait("DY_FreeBanner")
        return remaining_draws(self.text([1110,596,108,36]))

    def finish_draw(self):
        deadline = time.monotonic()+120
        attempts = {}
        last_action = {}
        result_seen = False
        while time.monotonic() < deadline:
            self.snap()
            if self.dismiss_notifications():
                continue
            if self.reco("DY_ObtainedHeader"):
                stage, target = "obtained", (640,602)
            elif self.reco("DY_RecruitResult") and np.median(self.image[145:170,110:130]) > 220:
                result_seen = True
                stage, target = "result", (1067,647)
            elif result_seen and self.reco("DY_FreeBanner"):
                return
            elif hit := self.reco("DY_RecruitCut"):
                # The ticket can cover SKIP. Cut it through its own visible prompt.
                stage = "cut"
                x, y, w, h = hit.box
                target = (x+w//2, y+h//2)
            elif hit := self.reco("DY_RecruitSkip"):
                stage = "skip"
                x, y, w, h = hit.box
                target = (x+w//2, y+h//2)
            elif self.reco("DY_MemberReveal"):
                stage, target = "member", (985,570)
            else:
                time.sleep(0.4)
                continue
            self.report["draw_stage"] = stage
            now = time.monotonic()
            # Re-recognize each time; retry only navigation, never the draw submission.
            if now-last_action.get(stage, float("-inf")) < 3:
                time.sleep(0.4)
                continue
            if attempts.get(stage, 0) >= 3:
                raise FlowError(f"招募阶段 {stage} 点击 3 次后仍未推进，不重复提交招募")
            attempts[stage] = attempts.get(stage, 0)+1
            last_action[stage] = now
            self.report["draw_attempts"] = dict(attempts)
            print(f"[免费招募] {stage}：第 {attempts[stage]}/3 次推进",flush=True)
            self.tap(*target)
        raise FlowError("招募结果未确认，不重复提交招募")

    def recruit(self):
        self.select_free_recruit()
        for _ in range(3):
            count = self.free_remaining()
            if count == 0:
                break
            if not self.hit_text([1176,652,77,39], "^免费$"):
                self.report["status"] = "free_draw_not_available"
                self.home()
                return
            self.tap(1188,657)
            self.wait("DY_FreeConfirm")
            verify_free_confirmation(self.text([479,270,327,94]))
            self.report["draw_in_flight"] = True
            self.tap(770,476)
            self.finish_draw()
            if self.free_remaining() != count-1:
                raise FlowError("免费招募剩余次数未减少，不重复提交")
            self.report["draw_in_flight"] = False
            self.report["draws"] += 1
            print(f"[免费招募] 已完成一次，剩余 {count-1} 次",flush=True)
        self.report["status"] = "finished"
        self.home()


class DailyAction(CustomAction):
    operation = ""

    def run(self, context, argv):
        flow = DailyFlow(context)
        output = Path("debug/daily")
        output.mkdir(parents=True,exist_ok=True)
        try:
            getattr(flow,self.operation)()
            return True
        except Exception as exc:
            flow.report.update(status="error",error=str(exc))
            print(f"[日常任务] {self.operation} 停止：{exc}",flush=True)
            return False
        finally:
            stamp = time.strftime("%Y%m%d-%H%M%S")
            if flow.report["status"] == "error" and flow.image is not None:
                from PIL import Image
                Image.fromarray(flow.image[:,:,::-1]).save(output/f"{self.operation}-{stamp}.png")
            (output/f"{self.operation}-{stamp}.json").write_text(json.dumps(flow.report,ensure_ascii=False,indent=2),encoding="utf-8")


class ClaimHomeGifts(DailyAction):
    operation = "gifts"


class ClaimHomeMissions(DailyAction):
    operation = "missions"


class ExchangeMichelle(DailyAction):
    operation = "exchange"


class DailyFreeRecruit(DailyAction):
    operation = "recruit"
