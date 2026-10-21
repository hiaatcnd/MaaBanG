using System;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using MaaFramework.Binding;
using MFAAvalonia.Helper.ValueType;
using Newtonsoft.Json.Linq;

namespace MFAAvalonia.Extensions.MaaFW;

public static class MaaBanGExitOptions
{
    public static JObject MergeParameters(string? value)
    {
        var raw = JToken.Parse(value ?? "{}");
        var result = new JObject();
        foreach (var part in raw is JArray array ? array : new JArray(raw))
        {
            if (part is not JObject patch)
                throw new InvalidOperationException("模拟器任务参数格式无效");
            result.Merge(patch, new JsonMergeSettings { MergeArrayHandling = MergeArrayHandling.Replace });
        }
        return result;
    }

    public static JObject Resolve(string? exit, string? wakeup, string? adb, string? address)
    {
        var parameters = MergeParameters(exit);
        var settings = (JObject?)parameters["EX_Settings"]?["attach"] ?? new JObject();
        var target = settings.Value<string>("target") ?? "follow";
        JObject connection, game;
        if (target == "follow" && wakeup != null)
        {
            var startup = MergeParameters(wakeup);
            connection = (JObject?)startup["SW_Connection"]?["attach"] ?? new JObject();
            game = (JObject?)startup["SW_Game"]?["attach"] ?? new JObject();
            // Use the actual connection selected by startup, including its resolved port.
            if (!string.IsNullOrWhiteSpace(adb)) connection["adb_path"] = adb;
            if (!string.IsNullOrWhiteSpace(address)) connection["address"] = address;
        }
        else if (target == "follow")
        {
            connection = new JObject { ["emulator"] = "none" };
            game = new JObject { ["package"] = "com.bilibili.star.bili", ["user"] = "0" };
        }
        else if (target == "manual")
        {
            connection = (JObject?)parameters["EX_Connection"]?["attach"] ?? new JObject();
            game = (JObject?)parameters["EX_Game"]?["attach"] ?? new JObject();
        }
        else throw new InvalidOperationException("退出目标设置无效");

        if (connection.Value<string>("emulator") == "none")
        {
            if (string.IsNullOrWhiteSpace(connection.Value<string>("adb_path"))) connection["adb_path"] = adb;
            if (string.IsNullOrWhiteSpace(connection.Value<string>("address"))) connection["address"] = address;
        }
        var result = (JObject)connection.DeepClone();
        result.Merge(game);
        result.Merge(settings);
        return result;
    }
}

public partial class MaaProcessor
{
    public bool HasOnlyExitTasks
    {
        get
        {
            var selected = ViewModel?.TaskItemViewModels.Where(t => t.IsChecked || t.IsCheckedWithNull == null).ToList();
            return selected?.Count > 0 && selected.All(t => t.InterfaceItem?.Entry == "ExitGame");
        }
    }

    private async Task<MaaJobStatus> RunExitAsync(NodeAndParam task, NodeAndParam? wakeup, CancellationToken token)
    {
        var options = MaaBanGExitOptions.Resolve(task.Param, wakeup?.Param, Config.AdbDevice.AdbPath, Config.AdbDevice.AdbSerial);
        if ((options.Value<string>("target") ?? "follow") == "follow" && wakeup == null &&
            ViewModel?.CurrentController != MaaControllerTypes.Adb)
            throw new InvalidOperationException("沿用当前设备需要选择安卓端控制器，或在退出任务中指定目标");
        var result = await RunLifecycleHostAsync("exit_host.py", options, token);
        if (result.Value<string>("status") != "finished")
            throw new InvalidOperationException("未确认退出完成，请检查 debug/exit 中的记录");
        // Closing an emulator invalidates the native connection. Retain the saved target.
        var closesCurrent = result.Value<string>("address") == Config.AdbDevice.AdbSerial ||
            result["closed_addresses"]?.Values<string>().Contains(Config.AdbDevice.AdbSerial) == true;
        if ((options.Value<string>("scope") ?? "app") != "app" && closesCurrent)
        {
            SetTasker();
            ViewModel?.SetConnected(false);
        }
        AddLog("[退出] 已完成");
        return MaaJobStatus.Succeeded;
    }
}
