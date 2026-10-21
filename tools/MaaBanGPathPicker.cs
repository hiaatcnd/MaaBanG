using System;
using System.IO;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Layout;
using Avalonia.Markup.Xaml.MarkupExtensions;
using Avalonia.Media;
using Avalonia.Platform.Storage;
using MFAAvalonia.Extensions.MaaFW;

namespace MFAAvalonia.Helper;

// Keep standard interface input fields and their existing save/override handling.
// Only the three startup program paths receive a file picker.
public static class MaaBanGPathPicker
{
    public static Control? CreateInputLayout(TextBox textBox, string? option,
        MaaInterface.MaaInterfaceOptionInput input)
    {
        if (option?.StartsWith("唤醒", StringComparison.Ordinal) != true &&
            option?.StartsWith("退出", StringComparison.Ordinal) != true) return null;
        // Stack labels, fields and hints: horizontal StackPanel labels were
        // measured without a width limit and could paint over the input column.
        var panel = new StackPanel
        {
            Margin = new Thickness(0, 4, 0, 8),
            Spacing = 4,
            HorizontalAlignment = HorizontalAlignment.Stretch
        };
        panel.Children.Add(new TextBlock
        {
            Text = input.DisplayName,
            FontSize = 14,
            TextWrapping = TextWrapping.Wrap
        });
        textBox.HorizontalAlignment = HorizontalAlignment.Stretch;
        panel.Children.Add(textBox);
        if (!string.IsNullOrWhiteSpace(input.Description))
        {
            var hint = new TextBlock
            {
                Text = input.Description,
                FontSize = 12,
                TextWrapping = TextWrapping.Wrap
            };
            hint.Bind(TextBlock.ForegroundProperty, new DynamicResourceExtension("SukiLowText"));
            panel.Children.Add(hint);
        }
        return panel;
    }

    public static void Attach(TextBox textBox, string? option, string? field)
    {
        // Exit uses the same MuMu/ADB path controls and validation.
        if (option?.StartsWith("退出", StringComparison.Ordinal) == true)
            option = "唤醒" + option.Substring(2);
        if (option == "唤醒连接设置" && field == "address")
        {
            textBox.Watermark = "MuMu 可留空，自动读取";
            return;
        }

        var title = (option, field) switch
        {
            ("唤醒MuMu设置", "emulator_path") => "选择 MuMu 的 mumu-cli.exe",
            ("唤醒自定义模拟器设置", "emulator_path") => "选择模拟器启动程序",
            ("唤醒连接设置", "adb_path") => "选择 ADB 程序",
            _ => null
        };
        if (title == null) return;

        textBox.Watermark = option == "唤醒自定义模拟器设置"
            ? "选择模拟器的 .exe 程序"
            : "MuMu 可留空，自动查找";
        var browse = new Button
        {
            Content = "选择…",
            Padding = new Thickness(8, 2),
            Margin = new Thickness(4, 0, 2, 0),
            MinHeight = 24,
            VerticalAlignment = VerticalAlignment.Center
        };
        ToolTip.SetTip(browse, title);
        textBox.InnerRightContent = browse;
        browse.Click += async (_, _) =>
        {
            if (!textBox.IsEffectivelyEnabled || !browse.IsEnabled) return;
            var storage = TopLevel.GetTopLevel(textBox)?.StorageProvider;
            if (storage?.CanOpen != true)
            {
                ToastHelper.Warn("无法选择文件", "当前环境不支持文件选择，请直接填写完整路径。");
                return;
            }
            browse.IsEnabled = false;
            try
            {
                var picker = new FilePickerOpenOptions
                {
                    Title = title,
                    AllowMultiple = false,
                    FileTypeFilter = [new FilePickerFileType("Windows 程序 (*.exe)") { Patterns = ["*.exe"] }]
                };
                var current = Environment.ExpandEnvironmentVariables((textBox.Text ?? "").Trim().Trim('"'));
                if (Path.IsPathFullyQualified(current) && Path.GetDirectoryName(current) is { } directory && Directory.Exists(directory))
                    picker.SuggestedStartLocation = await storage.TryGetFolderFromPathAsync(new Uri(directory));
                var files = await storage.OpenFilePickerAsync(picker);
                if (files.Count == 0 || !textBox.IsEffectivelyEnabled) return;
                var path = files[0].TryGetLocalPath();
                if (string.IsNullOrEmpty(path) || !File.Exists(path) ||
                    !string.Equals(Path.GetExtension(path), ".exe", StringComparison.OrdinalIgnoreCase))
                    throw new IOException("请选择本机的 .exe 程序。");
                if (option == "唤醒MuMu设置" &&
                    !string.Equals(Path.GetFileName(path), "mumu-cli.exe", StringComparison.OrdinalIgnoreCase))
                    throw new IOException("MuMu 模式请选择安装目录中的 mumu-cli.exe。");
                // TextChanged persists the path through the same route as manual input.
                textBox.Text = path;
            }
            catch (Exception error)
            {
                ToastHelper.Warn("选择文件失败", error.Message);
            }
            finally
            {
                browse.IsEnabled = true;
            }
        };
    }
}
