using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Headless;
using Avalonia.Interactivity;
using Avalonia.Input;
using Avalonia.Themes.Fluent;
using Avalonia.Threading;
using Avalonia.VisualTree;
using MFAAvalonia.Helper;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

class Program
{
    static void Assert(bool condition, string message) { if (!condition) throw new Exception(message); }
    static T Field<T>(object value, string name) => (T)value.GetType().GetField(name, BindingFlags.NonPublic | BindingFlags.Instance)!.GetValue(value)!;
    static object? Invoke(object value, string name, params object[] args) => value.GetType().GetMethod(name, BindingFlags.NonPublic | BindingFlags.Instance)!.Invoke(value, args);
    static void Pump(Func<bool> done, int seconds = 30)
    {
        var timer = Stopwatch.StartNew();
        while (!done()) { Dispatcher.UIThread.RunJobs(); Thread.Sleep(10); if (timer.Elapsed.TotalSeconds > seconds) throw new TimeoutException("UI operation timed out"); }
        Dispatcher.UIThread.RunJobs();
    }
    static void Wait(Task task) { Pump(() => task.IsCompleted); task.GetAwaiter().GetResult(); }
    static T Wait<T>(Task<T> task) { Wait((Task)task); return task.Result; }
    static void Click(MaaBanGBestdoriView view, string label)
    {
        var button = view.GetVisualDescendants().OfType<Button>().Single(b => b.Content?.ToString() == label);
        Assert(button.IsEnabled, label + " disabled"); button.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
        Pump(() => !Field<bool>(view, "_operation"));
    }
    static JObject State(MaaBanGBestdoriClient client) => Wait(client.CallAsync("state"));
    static JObject Card(JObject state) => (JObject)state["result"]!["profile"]!["cards"]![0]!;
    static void Capture(Window window, string path)
    {
        for (var i = 0; i < 3; i++) { Dispatcher.UIThread.RunJobs(); AvaloniaHeadlessPlatform.ForceRenderTimerTick(1); Thread.Sleep(10); }
        window.CaptureRenderedFrame()!.Save(path);
    }
    [STAThread]
    static int Main(string[] args)
    {
        var root = Path.GetFullPath(args[0]);
        var output = Environment.GetEnvironmentVariable("MAABANG_UI_TEST_OUTPUT") ?? Path.Combine(root, "debug", "bestdori-native-ui");
        Directory.CreateDirectory(output);
        Environment.SetEnvironmentVariable("MAABANG_DATA_DIR", Path.Combine(output, "profile-" + Guid.NewGuid().ToString("N")));
        AppBuilder.Configure<Application>().UseSkia().UseHeadless(new AvaloniaHeadlessPlatformOptions { UseHeadlessDrawing = false }).SetupWithoutStarting();
        Application.Current!.Styles.Add(new FluentTheme());
        Application.Current.Styles.Add(new FluentAvalonia.Styling.FluentAvaloniaTheme());
        Application.Current.Styles.Add(new SukiUI.SukiTheme());
        var client = new MaaBanGBestdoriClient();
        Window? window = null;
        try
        {
            Wait(client.StartAsync(args[1], Path.Combine(root, "agent", "bestdori_desktop.py")));
            var view = new MaaBanGBestdoriView();
            typeof(MaaBanGBestdoriView).GetField("_client", BindingFlags.NonPublic | BindingFlags.Instance)!.SetValue(view, client);
            typeof(MaaBanGBestdoriView).GetField("_catalog", BindingFlags.NonPublic | BindingFlags.Instance)!.SetValue(view, Wait(client.CallAsync("catalog")));
            Invoke(view, "BuildScope");
            window = new Window { Content = view, Width = 1280, Height = 850, FontFamily = new Avalonia.Media.FontFamily("Microsoft YaHei UI"), Title = "MaaBanG · Bestdori 原生工具验证" };
            window.Show(); Dispatcher.UIThread.RunJobs();
            Assert(Field<Dictionary<int, CheckBox>>(view, "_members").Count == 40, "member choices missing");
            Assert(Field<Dictionary<int, CheckBox>>(view, "_bands").Count == 8, "band choices missing");
            var members = Field<Dictionary<int, CheckBox>>(view, "_members");
            var bands = Field<Dictionary<int, CheckBox>>(view, "_bands");
            foreach (var check in members.Values) check.IsChecked = false;
            bands[21].IsChecked = true;
            Assert(members.Where(p => p.Value.IsChecked == true).Select(p => p.Key).Order().SequenceEqual(new[] {26,27,28,29,30}), "band selection did not select exactly its members");
            members[26].IsChecked = false;
            Assert(bands[21].IsChecked == null, "partial band missing");
            typeof(MaaBanGBestdoriBandCheckBox).GetMethod("Toggle", BindingFlags.NonPublic | BindingFlags.Instance)!.Invoke(bands[21], null);
            Assert(members[26].IsChecked == true && bands[21].IsChecked == true, "clicking partial band must select all");
            foreach (var check in members.Values) check.IsChecked = false;
            Field<TabControl>(view, "_tabs").SelectedIndex = 1; Dispatcher.UIThread.RunJobs();
            Click(view, "读取游戏");
            Assert(Field<TextBlock>(view, "_scanSummary").Text!.Contains("未开始读取"), "empty scope lacks inline feedback");
            Assert(Field<TextBlock>(view, "_status").Text!.Contains("成员"), "empty scope lacks prominent feedback");
            foreach (var check in members.Values) check.IsChecked = true;
            Invoke(view, "RenderAccount", "界面验证账号");
            Assert(Field<TextBlock>(view,"_accountBadgeText").Text!.Contains("已登录"), "persistent signed-in status missing");
            Assert(!Field<StackPanel>(view,"_loginForm").IsVisible, "login form remains after login");
            Invoke(view, "RenderAccount", new object[] { null! });
            foreach (var check in Field<Dictionary<int, CheckBox>>(view, "_rarities")) check.Value.IsChecked = check.Key == 5;
            foreach (var key in new[] { "_maxLevel", "_maxSkill", "_maxMaster" }) Field<CheckBox>(view, key).IsChecked = true;
            Invoke(view, "AddRow", new JObject { ["id"] = 1703, ["level"] = 35, ["skill"] = 1, ["master"] = 0, ["train"] = 0 });
            var first = Field<ObservableCollection<MaaBanGBestdoriRow>>(view,"_rows")[0];
            var tabs = Field<TabControl>(view, "_tabs"); tabs.SelectedIndex = 1; Dispatcher.UIThread.RunJobs();
            Invoke(view,"AddRow",new JObject { ["id"] = 1704, ["level"] = 12, ["skill"] = 1, ["master"] = 0, ["train"] = 0, ["selected"] = false });
            var rows = Field<ObservableCollection<MaaBanGBestdoriRow>>(view,"_rows");
            Click(view,"提升勾选卡牌");
            Assert(first.LevelDisplay == "60" && first.SkillDisplay == "5" && first.MasterDisplay == "4", "maximum values must be visible numbers");
            Assert(rows[1].Level == "12" && rows[1].Skill == "2" && rows[1].Master == "0", "unselected card was maximized");
            Click(view,"还原勾选卡牌");
            Assert(first.Level == "35" && first.Skill == "2" && first.Master == "0" && first.Train == "0", "restore lost original scan values");
            Field<NumericUpDown>(view,"_targetLevel").Value = 20;
            Field<NumericUpDown>(view,"_targetSkill").Value = 1;
            first.LevelDisplay = "10"; first.SkillDisplay = "1";
            Click(view,"提升勾选卡牌");
            Assert(first.Level == "35" && first.Skill == "2", "target ignored original scan values or lowered a field");
            Field<NumericUpDown>(view,"_targetLevel").Value = 60;
            Field<NumericUpDown>(view,"_targetSkill").Value = 5;
            Click(view,"提升勾选卡牌");
            // Exercise the actual cell editor, including an empty intermediate value.
            var table = Field<DataGrid>(view,"_table");
            Dispatcher.UIThread.RunJobs();
            var numericEditor = table.GetVisualDescendants().OfType<TextBox>().Single(t => t.Name == "BestdoriTargetSkill" && ReferenceEquals(t.DataContext,first));
            numericEditor.Focus(); Dispatcher.UIThread.RunJobs();
            Assert(numericEditor.Bounds.Height <= 30 && numericEditor.Bounds.Width <= 66, "target editor stretched across comparison rows");
            Assert(table.GetVisualDescendants().OfType<TextBlock>().Any(t => ReferenceEquals(t.DataContext,first) && t.Text == first.ReadSkillText), "read reference hidden during editing");
            numericEditor.Text = ""; Dispatcher.UIThread.RunJobs();
            Assert(numericEditor.Text == "", "clearing numeric editor injected the missing-value label");
            Capture(window, Path.Combine(output, "target-editing.png"));
            numericEditor.Text = "3"; numericEditor.RaiseEvent(new KeyEventArgs { RoutedEvent = InputElement.KeyDownEvent, Key = Key.Escape });
            Assert(first.Skill == "5" && numericEditor.Text == "5", "escape changed target value");
            numericEditor.Focus(); numericEditor.Text = "9"; numericEditor.RaiseEvent(new KeyEventArgs { RoutedEvent = InputElement.KeyDownEvent, Key = Key.Enter });
            Assert(first.Skill == "5", "out-of-range skill accepted");
            numericEditor.Text = "5"; numericEditor.RaiseEvent(new KeyEventArgs { RoutedEvent = InputElement.KeyDownEvent, Key = Key.Enter });
            Click(view, "查看合并结果");
            var card = Card(State(client));
            Assert(card.Value<int>("level") == 60 && card.Value<int>("skill") == 4 && card.Value<int>("master") == 4, "all-max preview incorrect");
            Assert(Field<Button>(view, "_export").IsEnabled, "export not enabled after preview: " + Field<TextBlock>(view, "_status").Text);
            first.SkillText = "技能资料已加载";
            Assert(Field<Button>(view, "_export").IsEnabled, "media load invalidated merge result");
            Field<CheckBox>(view, "_maxSkill").IsChecked = false;
            Assert(Field<Button>(view, "_export").IsEnabled, "changing pending batch options must not mutate cards");
            rows[0].SkillDisplay = "";
            Assert(!Field<Button>(view, "_export").IsEnabled, "editing row failed to invalidate preview");
            Assert(rows[0].SkillDisplay == "未读取", "missing value lacks clear label");
            Click(view, "重新计算结果");
            Assert(((JArray)State(client)["result"]!["skipped"]!).Count == 1, "missing unforced stat must skip new card");
            rows[0].Skill = "2";
            Click(view, "重新计算结果");
            Assert(Card(State(client)).Value<int>("skill") == 1, "skill display conversion incorrect");
            Wait((Task)Invoke(view,"SaveReviewAsync")!);
            rows.Clear(); Wait((Task)Invoke(view,"RestoreReviewAsync")!);
            Assert(rows.Count == 2 && rows[0].Level == "60" && !rows[1].Selected, "review cache lost values or selection");
            Assert(rows[0].OriginalValues.Value<int>("level") == 35 && rows[0].OriginalValues.Value<int>("skill") == 1,
                   "review cache replaced original readings with adjusted values");
            first=rows[0]; rows.RemoveAt(1);

            var exported = Wait(client.CallAsync("export", new JObject { ["token"] = State(client)["result_token"]!.DeepClone() }));
            var baseline = JObject.Parse(exported.Value<string>("text")!);
            baseline["name"] = "已有档案";
            baseline["cards"]![0]!["skill"] = 4;
            ((JArray)baseline["cards"]!).Add(new JObject { ["id"] = 2, ["level"] = 20, ["skill"] = 0, ["master"] = 0, ["train"] = 0, ["art"] = 0, ["ep"] = 2, ["exclude"] = true });
            baseline["items"]!["PoppinParty"]![0] = 7;
            tabs.SelectedIndex = 0; Dispatcher.UIThread.RunJobs();
            Wait((Task)Invoke(view, "ImportAsync", baseline.ToString(Formatting.None))!);
            tabs.SelectedIndex = 2; Dispatcher.UIThread.RunJobs();
            Click(view, "重新计算结果");
            Assert(Card(State(client)).Value<int>("skill") == 4, "max merge lost baseline");
            Assert(first.BaseSkillText == "5" && first.ReadSkillText == "2" && first.GoalSkillText == "5",
                   "baseline/read/target comparison does not reflect max merge");
            Field<CheckBox>(view,"_onlyChanged").IsChecked = true;
            Assert(!Field<Avalonia.Collections.DataGridCollectionView>(view,"_rowView").Cast<object>().Any(),
                   "unchanged merged row was not filtered out");
            Field<ComboBox>(view, "_policy").SelectedIndex = 1;
            Assert(!Field<Button>(view, "_export").IsEnabled, "policy change failed to invalidate preview");
            Click(view, "重新计算结果");
            Assert(first.GoalSkillText == "2" && Field<Avalonia.Collections.DataGridCollectionView>(view,"_rowView").Cast<object>().Count() == 1,
                   "filter did not follow overwrite result");
            Field<CheckBox>(view,"_onlyChanged").IsChecked = false;
            var merged = State(client)["result"]!["profile"]!;
            Assert(merged["cards"]![0]!.Value<int>("skill") == 1, "overwrite did not use current skill");
            Assert(JToken.DeepEquals(merged["cards"]![1], baseline["cards"]![1]), "out-of-range card modified");
            Assert(JToken.DeepEquals(merged["items"], baseline["items"]), "items modified");
            Dispatcher.UIThread.RunJobs();
            Assert(Field<StackPanel>(view,"_preview").GetVisualDescendants().OfType<TextBlock>().Any(t => t.Text == "技能 5 → 2"),
                   "changed preview value lacks before/after marking");
            Capture(window, Path.Combine(output, "preview.png"));
            tabs.SelectedIndex = 1; Dispatcher.UIThread.RunJobs();
            Invoke(view, "AddRow", new JObject { ["id"] = 1704, ["conflict"] = true });
            var conflict = rows.Last();
            Click(view,"全选已确认卡牌");
            Assert(!conflict.Selected, "bulk selection accepted conflicting readings");
            rows.Remove(conflict);
            Invoke(view, "AddRow", new JObject { ["id"] = null, ["title"] = "可爱的朋友", ["member"] = "市谷有咲", ["skill"] = 3, ["master"] = 0, ["train"] = 1 });
            var unknown = rows.Last();
            Assert(!unknown.Selected && unknown.Id == "", "unresolved identity was selected");
            try { unknown.Observation(true,false,true); throw new Exception("unresolved identity accepted"); }
            catch (InvalidOperationException ex) { Assert(ex.Message.Contains("身份尚未确认"), "opaque card-ID error"); }
            Field<DataGrid>(view,"_table").SelectedItem = unknown;
            Field<TextBox>(view,"_search").Text = "可爱的朋友";
            var resolver = view.GetVisualDescendants().OfType<Expander>().Single(e => e.Header?.ToString() == "搜索卡牌 / 确认身份");
            resolver.IsExpanded = true; Dispatcher.UIThread.RunJobs();
            Click(view,"确认当前记录身份");
            Assert(unknown.Id == "20" && unknown.Selected, "search identity confirmation failed");
            resolver.IsExpanded = false;
            Wait((Task)Invoke(view,"LoadMediaAsync",unknown)!);
            Assert(unknown.Artwork != null && unknown.SkillText.Contains("停下来咯"), "real card art/skill unavailable: " + unknown.SkillText);
            Assert(unknown.NormalArtwork != null,"both card thumbnail variants must be displayed");
            Wait((Task)Invoke(view,"LoadMediaAsync",first)!);
            Pump(() => Field<SemaphoreSlim>(view,"_mediaSlots").CurrentCount == 3);
            var skillAtTwo = first.SkillText;
            first.Skill = "5";
            Assert(!first.SkillText.Contains("满级参考") && first.SkillText != skillAtTwo, "skill effect did not follow actual level");
            first.Skill = "2";
            Assert(first.SkillText == skillAtTwo, "skill change unexpectedly requires a network refresh");
            Invoke(view, "RefreshComparison"); Field<DispatcherTimer>(view,"_comparisonTimer").Stop();
            // A single thumbnail must keep the same text origin as a paired one.
            first.NormalArtwork = null;
            first.SkillText = "多行技能显示验证\n第一项效果\n第二项效果\n第三项条件\n第四项条件\n第五项条件\n第六项条件";
            Dispatcher.UIThread.RunJobs();
            var firstSkill = view.GetVisualDescendants().OfType<TextBlock>().Single(t => t.Text == first.SkillText);
            var otherSkill = view.GetVisualDescendants().OfType<TextBlock>().Single(t => t.Text == unknown.SkillText);
            Assert(Math.Abs(firstSkill.TranslatePoint(default, view)!.Value.X - otherSkill.TranslatePoint(default, view)!.Value.X) < 1,
                   "single and double artwork skill columns are not aligned");
            Assert(firstSkill.MaxLines == 0 && firstSkill.Bounds.Height > 90, "full skill description clipped");
            var firstVisualRow = firstSkill.GetVisualAncestors().OfType<DataGridRow>().First();
            Assert(firstVisualRow.Bounds.Height >= firstSkill.Bounds.Height + 25, "row did not grow to fit skill text");
            Capture(window, Path.Combine(output, "review-long-skill.png"));
            first.Skill = "2";
            Field<TextBlock>(view,"_scanSummary").Text = "核对记录：2 张 · 卡面和技能资料来自 Bestdori";
            Dispatcher.UIThread.RunJobs();
            Capture(window, Path.Combine(output, "review.png"));
            tabs.SelectedIndex = 0; Dispatcher.UIThread.RunJobs();
            Capture(window, Path.Combine(output, "settings.png"));
            tabs.SelectedIndex=3; Invoke(view,"RenderAccount","界面验证账号"); Dispatcher.UIThread.RunJobs();
            Capture(window, Path.Combine(output,"account.png"));
            Assert(Field<Button>(view,"_logout").Bounds.Width < 200,"logout stretches across window");
            Invoke(view,"RenderAccount",new object[] {null!});
            window.Width=1920; tabs.SelectedIndex=1; Dispatcher.UIThread.RunJobs();
            Capture(window, Path.Combine(output,"review-wide.png"));
            Assert(Field<DataGrid>(view,"_table").Columns[1].ActualWidth <= 560,"card info column stretched");
            window.Width = 600; window.Height = 600; Dispatcher.UIThread.RunJobs();
            Capture(window, Path.Combine(output, "compact.png"));
            var fixture = Environment.GetEnvironmentVariable("MAABANG_UI_CLOUD_FIXTURE");
            if (!string.IsNullOrEmpty(fixture))
            {
                Environment.SetEnvironmentVariable("MAABANG_UI_TEST_AGENT", Path.Combine(root, "agent"));
                var mockClient = new MaaBanGBestdoriClient();
                try
                {
                    Wait(mockClient.StartAsync(args[1], fixture));
                    typeof(MaaBanGBestdoriView).GetField("_client", BindingFlags.NonPublic | BindingFlags.Instance)!.SetValue(view, mockClient);
                    Wait((Task)Invoke(view, "RefreshAsync")!);
                    window.Width = 1280; window.Height = 850; tabs.SelectedIndex = 3; Dispatcher.UIThread.RunJobs();
                    Click(view, "读取云端档案");
                    var before = (JArray)Wait(mockClient.CallAsync("cloud", new JObject()))["profiles"]!;
                    Field<ComboBox>(view, "_cloud").SelectedIndex = 1;
                    Click(view, "使用所选底稿");
                    Assert(Field<TextBox>(view, "_name").Text == "要更新的档案", "replacement did not keep cloud name");
                    Assert(!Field<Button>(view, "_replace").IsEnabled, "replacement enabled without a preview");
                    rows.Clear();
                    Invoke(view, "AddRow", new JObject { ["id"] = 1703, ["level"] = 60, ["skill"] = 4, ["master"] = 4, ["train"] = 1 });
                    tabs.SelectedIndex = 2; Dispatcher.UIThread.RunJobs(); Click(view, "重新计算结果");
                    Assert(Field<TextBlock>(view, "_replaceTarget").Text!.Contains("要更新的档案"), "target missing from save view");
                    Dispatcher.UIThread.RunJobs();
                    Capture(window, Path.Combine(output, "replace.png"));
                    Click(view, "替换当前云端底稿");
                    Assert(Field<TextBlock>(view, "_status").Text!.StartsWith("已回读确认替换"), "mock cloud replace failed");
                    var after = (JArray)Wait(mockClient.CallAsync("cloud", new JObject()))["profiles"]!;
                    Assert(after.Count == before.Count && JToken.DeepEquals(before[0], after[0]) && !JToken.DeepEquals(before[1], after[1]),
                           "replacement changed the wrong profile or appended");
                    Assert(!Field<Button>(view, "_replace").IsEnabled, "old replacement can be replayed");
                    Console.WriteLine("PASS: native replace flow against in-memory cloud; target, preview, preserve others, readback, no replay.");
                }
                finally
                {
                    mockClient.Disconnect();
                    typeof(MaaBanGBestdoriView).GetField("_client", BindingFlags.NonPublic | BindingFlags.Instance)!.SetValue(view, client);
                    Wait((Task)Invoke(view, "RefreshAsync")!);
                }
            }
            if (args.Length > 2)
            {
                // Explicitly requested account test. Never write credentials or
                // account screenshots to logs/artifacts; cloud gets one named test.
                var credentialLines = File.ReadAllLines(args[2]);
                Assert(credentialLines.Length >= 2, "account file needs two lines");
                tabs.SelectedIndex = 3; Dispatcher.UIThread.RunJobs();
                Field<TextBox>(view, "_username").Text = credentialLines[0].Trim();
                Field<TextBox>(view, "_password").Text = credentialLines[1];
                Array.Clear(credentialLines);
                Click(view, "登录");
                Assert(!string.IsNullOrEmpty(State(client).Value<string>("username")), "real login failed: " + Field<TextBlock>(view, "_status").Text);
                Assert(Field<TextBox>(view, "_password").Text == "", "password not cleared after login");
                Click(view, "读取云端档案");
                var before = (JArray)Wait(client.CallAsync("cloud", new JObject()))["profiles"]!;
                if (before.Count > 0)
                {
                    Field<ComboBox>(view, "_cloud").SelectedIndex = 0;
                    Click(view, "使用所选底稿");
                    Assert(State(client).Value<string>("base_name") == before[0].Value<string>("name"), "cloud base selection failed");
                }
                // Upload actual previously-read values in a clearly named test
                // profile, not the artificial low skill used by the merge tests.
                tabs.SelectedIndex = 0; Dispatcher.UIThread.RunJobs(); Click(view, "从空白档案开始");
                rows.Clear();
                var actual = JObject.Parse(File.ReadAllText(Path.Combine(root, "debug", "bestdori-inspect", "actual-values-scan.json")));
                foreach (var row in (JArray)actual["observations"]!) Invoke(view, "AddRow", (JObject)row);
                foreach (var key in new[] { "_maxLevel", "_maxSkill", "_maxMaster" }) Field<CheckBox>(view, key).IsChecked = false;
                var testName = "MaaBanG 联调测试 " + DateTime.Now.ToString("yyyyMMdd-HHmmss");
                Field<TextBox>(view, "_name").Text = testName;
                tabs.SelectedIndex = 2; Dispatcher.UIThread.RunJobs(); Click(view, "重新计算结果");
                Assert(((JArray)State(client)["result"]!["profile"]!["cards"]!).Count == 3, "actual test cards missing");
                Click(view, "新增云端档案");
                Assert(Field<TextBlock>(view, "_status").Text!.StartsWith("已回读确认"), "cloud append not confirmed: " + Field<TextBlock>(view, "_status").Text);
                var after = (JArray)Wait(client.CallAsync("cloud", new JObject()))["profiles"]!;
                Assert(after.Count == before.Count + 1 && after.Last!.Value<string>("name") == testName, "cloud append readback mismatch");
                Assert(before.Select(v => v.Value<string>("key")).SequenceEqual(after.Take(before.Count).Select(v => v.Value<string>("key"))), "existing cloud profiles changed");
                tabs.SelectedIndex = 3; Dispatcher.UIThread.RunJobs(); Click(view, "退出登录");
                Assert(State(client)["username"]!.Type == JTokenType.Null, "logout failed");
                File.WriteAllText(Path.Combine(output, "account-test.json"), new JObject { ["login"] = true,
                    ["cloud_base"] = before.Count > 0, ["before_count"] = before.Count, ["after_count"] = after.Count,
                    ["created_profile"] = testName, ["cards"] = 3, ["existing_unchanged"] = true, ["logout"] = true }.ToString());
                Console.WriteLine("PASS: real login, cloud list/base, append/readback, original profiles unchanged, logout.");
            }
            Console.WriteLine("PASS: native view + real service; target max, original restore/cache, raw numeric editor, full skill, artwork alignment, preview marking, scope preservation, file codec.");
            Console.WriteLine("Native screenshots: " + output);
            return 0;
        }
        catch (Exception ex) { Console.Error.WriteLine(ex); return 1; }
        finally { window?.Close(); client.Disconnect(); }
    }
}
