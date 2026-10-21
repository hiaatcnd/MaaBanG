using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Headless;
using Avalonia.Media;
using Avalonia.Themes.Fluent;
using Avalonia.Threading;
using MFAAvalonia.Extensions.MaaFW;
using MFAAvalonia.Helper;
using Newtonsoft.Json.Linq;

// Render the same input rows used by both desktop task option views. Check real
// measured bounds at narrow and wide widths, including wrapping hint text.
class Program
{
    static void Assert(bool condition, string message)
    {
        if (!condition) throw new Exception(message);
    }

    [STAThread]
    static int Main(string[] args)
    {
        var root = Path.GetFullPath(args[0]);
        var output = Path.Combine(root, "debug", "wakeup-input-layout");
        Directory.CreateDirectory(output);
        AppBuilder.Configure<Application>().UseSkia()
            .UseHeadless(new AvaloniaHeadlessPlatformOptions { UseHeadlessDrawing = false }).SetupWithoutStarting();
        Application.Current!.Styles.Add(new FluentTheme());
        Application.Current.Styles.Add(new FluentAvalonia.Styling.FluentAvaloniaTheme());
        Application.Current.Styles.Add(new SukiUI.SukiTheme());
        var options = JObject.Parse(File.ReadAllText(Path.Combine(root, "assets", "interface.json")))["option"]!;
        VerifyExitTargets();
        foreach (var width in new[] { 360, 760 })
        {
            var stack = new StackPanel { Margin = new Thickness(18), Spacing = 8 };
            var rows = new List<StackPanel>();
            var pickers = 0;
            foreach (var name in new[] { "唤醒MuMu设置", "唤醒连接设置", "唤醒游戏应用", "唤醒自定义模拟器设置",
                "退出MuMu设置", "退出连接设置", "退出游戏应用", "退出等待设置" })
            {
                stack.Children.Add(new TextBlock { Text = name, FontSize = 18, FontWeight = FontWeight.SemiBold });
                foreach (var token in options[name]!["inputs"]!)
                {
                    var input = token.ToObject<MaaInterface.MaaInterfaceOptionInput>()!;
                    var box = new TextBox { Text = input.Default, MinWidth = 120 };
                    MaaBanGPathPicker.Attach(box, name, input.Name);
                    if (box.InnerRightContent is Button) pickers++;
                    var row = (StackPanel)MaaBanGPathPicker.CreateInputLayout(box, name, input)!;
                    rows.Add(row);
                    stack.Children.Add(row);
                }
            }
            Assert(pickers == 5, "Every emulator/ADB path must have its own picker");
            var window = new Window
            {
                Width = width, Height = 1500, Content = stack,
                FontFamily = new FontFamily("Microsoft YaHei UI")
            };
            window.Show();
            Dispatcher.UIThread.RunJobs();
            window.Height = stack.DesiredSize.Height + 36;
            for (var i = 0; i < 3; i++)
            {
                Dispatcher.UIThread.RunJobs();
                AvaloniaHeadlessPlatform.ForceRenderTimerTick(1);
            }
            foreach (var row in rows)
            {
                Assert(row.Bounds.Width > 0, "Row was not laid out");
                for (var i = 0; i < row.Children.Count; i++)
                {
                    var child = row.Children[i];
                    Assert(child.Bounds.Height > 0, "Missing label, field or hint");
                    Assert(child.Bounds.Left >= 0 && child.Bounds.Right <= row.Bounds.Width + .5, "Input row overflow");
                    if (i > 0)
                        Assert(row.Children[i - 1].Bounds.Bottom <= child.Bounds.Top, "Label/hint overlaps an input");
                }
                if (row.Children.Last() is TextBlock hint && row.Children.Count > 2)
                    Assert(hint.TextWrapping == TextWrapping.Wrap, "Hint must wrap at the panel width");
            }
            window.CaptureRenderedFrame()!.Save(Path.Combine(output, $"inputs-{width}.png"));
            window.Close();
            Console.WriteLine($"PASS: {width}px, {rows.Count} rows, no label/input/hint overlap, {pickers} path pickers");
        }
        return 0;
    }

    static void VerifyExitTargets()
    {
        var exit = """
            [{"EX_Settings":{"attach":{"target":"follow","scope":"app"}}},
             {"EX_Game":{"attach":{"user":"0"}}}]
            """;
        var wake = """
            [{"SW_Connection":{"attach":{"emulator":"mumu","instance":"1","address":""}},
              "SW_Game":{"attach":{"package":"com.bilibili.star.bili","user":"0"}}},
             {"SW_Game":{"attach":{"user":"10"}}}]
            """;
        var inherited = MaaBanGExitOptions.Resolve(exit, wake, "actual-adb.exe", "127.0.0.1:16416");
        Assert(inherited.Value<string>("user") == "10", "Follow must preserve the selected clone, not the exit default user");
        Assert(inherited.Value<string>("instance") == "1" && inherited.Value<string>("address") == "127.0.0.1:16416", "Follow must use the actual startup device");
        var current = MaaBanGExitOptions.Resolve(exit, null, "saved-adb.exe", "emulator-5554");
        Assert(current.Value<string>("emulator") == "none" && current.Value<string>("address") == "emulator-5554", "No startup must follow current device without guessing instance 0");
        Assert(current.Value<string>("user") == "0", "Current device defaults to original application");
        var manual = """
            [{"EX_Settings":{"attach":{"target":"manual","scope":"instance"}},
              "EX_Connection":{"attach":{"emulator":"mumu","instance":"3","address":""}},
              "EX_Game":{"attach":{"user":"0"}}}]
            """;
        var selected = MaaBanGExitOptions.Resolve(manual, wake, "other-adb.exe", "127.0.0.1:16416");
        Assert(selected.Value<string>("instance") == "3" && selected.Value<string>("address") == "", "Manual MuMu must not inherit another instance's address");
        Assert(selected.Value<string>("user") == "0", "Manual game must not inherit startup clone");
        Console.WriteLine("PASS: exit target inheritance, ordered option overrides, current device and manual target isolation");
    }
}
