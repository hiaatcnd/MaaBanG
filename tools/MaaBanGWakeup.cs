using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using MaaFramework.Binding;
using MFAAvalonia.Helper;
using MFAAvalonia.Helper.ValueType;
using Newtonsoft.Json.Linq;

namespace MFAAvalonia.Extensions.MaaFW;

public partial class MaaProcessor
{
    public bool HasSelectedWakeup => ViewModel?.TaskItemViewModels.Any(t =>
        t.InterfaceItem?.Entry == "StartWakeup" && (t.IsChecked || t.IsCheckedWithNull == null)) == true;

    private static List<DragItemViewModel> WakeupFirst(List<DragItemViewModel> tasks)
    {
        if (tasks.Count(t => t.InterfaceItem?.Entry == "StartWakeup") > 1)
            throw new InvalidOperationException("每个队列只允许一个开始唤醒任务，请拆分为不同实例");
        if (tasks.Count(t => t.InterfaceItem?.Entry == "ExitGame") > 1)
            throw new InvalidOperationException("每个队列只能有一个退出任务");
        return tasks.OrderBy(t => t.InterfaceItem?.Entry switch
        {
            "StartWakeup" => 0, "ExitGame" => 2, _ => 1
        }).ToList();
    }

    private async Task PrepareWakeupAsync(NodeAndParam task, CancellationToken token)
    {
        if (ViewModel?.CurrentController != MaaControllerTypes.Adb)
            throw new InvalidOperationException("开始唤醒需要安卓端控制器");
        // MFA serializes the base node and selected options as an ordered array
        // of overrides. Resolve it before the Maa resource/controller exists.
        var parameters = MaaBanGExitOptions.MergeParameters(task.Param);
        var options = (JObject?)parameters["SW_Connection"]?["attach"] ?? new JObject();
        // Blank fields reuse this instance's saved connection for existing/custom devices.
        // MuMu discovers the exact configured VM through its CLI instead.
        if (options.Value<string>("emulator") == "none")
        {
            if (string.IsNullOrWhiteSpace(options.Value<string>("adb_path")))
                options["adb_path"] = Config.AdbDevice.AdbPath;
            if (string.IsNullOrWhiteSpace(options.Value<string>("address")))
                options["address"] = Config.AdbDevice.AdbSerial;
        }
        var connection = await RunLifecycleHostAsync("wakeup_host.py", options, token);
        var adb = connection.Value<string>("adb_path") ?? throw new IOException("唤醒未返回 ADB 程序");
        var address = connection.Value<string>("address") ?? throw new IOException("唤醒未返回连接地址");

        await _connectionGate.WaitAsync(token);
        Interlocked.Exchange(ref _isConnecting, 1);
        try
        {
            SetTasker();
            // Discover enhancements, but never substitute another serial or retain stale VM extras.
            var devices = await Task.Run(() => Toolkit.AdbDevice.Find(), token);
            var found = devices.FirstOrDefault(d => d.AdbSerial == address);
            var selected = new AdbDeviceInfo(found?.Name ?? address, adb, address,
                found?.ScreencapMethods ?? AdbScreencapMethods.Default,
                found?.InputMethods ?? AdbInputMethods.Default, found?.Config ?? "{}");
            Config.AdbDevice.AdbPath = adb;
            Config.AdbDevice.AdbSerial = address;
            Config.AdbDevice.Name = selected.Name;
            Config.AdbDevice.Config = selected.Config;
            Config.AdbDevice.Info = selected;
            ViewModel.SetConnected(false);
            var tasker = await GetTaskerAsync(token);
            if (tasker?.Controller?.IsConnected != true)
                throw new InvalidOperationException($"无法连接唤醒指定设备：{address}");
            ViewModel.SetConnected(true);
            ViewModel.SelectWakeupAdbDevice(selected);
            AddLog($"[开始唤醒] 已连接 {address}");
        }
        finally
        {
            Interlocked.Exchange(ref _isConnecting, 0);
            _connectionGate.Release();
        }
    }

    private async Task<JObject> RunLifecycleHostAsync(string script, JObject options, CancellationToken token)
    {
        var python = Path.Combine(AppPaths.DataRoot, "python", "python.exe");
        if (!File.Exists(python))
            python = Path.Combine(AppPaths.AgentDirectory, ".venv", "Scripts", "python.exe");
        var start = new ProcessStartInfo(python)
        {
            WorkingDirectory = AppPaths.DataRoot, UseShellExecute = false, CreateNoWindow = true,
            RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true,
            StandardInputEncoding = new UTF8Encoding(false),
            StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8,
        };
        start.ArgumentList.Add("-u");
        start.ArgumentList.Add(Path.Combine(AppPaths.AgentDirectory, script));
        start.Environment["PYTHONUTF8"] = "1";
        using var process = Process.Start(start) ?? throw new IOException("无法启动模拟器任务程序");
        // Do not kill the emulator process tree on Stop: only stop the bootstrap waiter.
        using var cancel = token.Register(() =>
        {
            try { if (!process.HasExited) process.Kill(); }
            catch (InvalidOperationException) { }
        });
        var output = process.StandardOutput.ReadToEndAsync();
        var errors = new StringBuilder();
        async Task Drain()
        {
            while (await process.StandardError.ReadLineAsync() is { } line)
            {
                AddLog(line);
                if (errors.Length < 4000) errors.AppendLine(line);
            }
        }
        var drain = Drain();
        await process.StandardInput.WriteAsync(options.ToString());
        process.StandardInput.Close();
        await process.WaitForExitAsync(token);
        await drain;
        token.ThrowIfCancellationRequested();
        if (process.ExitCode != 0)
            throw new InvalidOperationException(errors.ToString());
        return JObject.Parse(await output);
    }

}
