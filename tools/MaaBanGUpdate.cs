using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Threading.Tasks;
using Newtonsoft.Json.Linq;

namespace MFAAvalonia.Helper;

// Included in our pinned MFA build. Downloads, notifications and preferences
// stay in MFA; only preparing and replacing the full package is project-specific.
internal static class MaaBanGUpdate
{
    public static async Task<string> PrepareAsync(string archive, string expectedVersion)
    {
        var app = AppPaths.InstallRoot;
        var root = Directory.GetParent(app)?.FullName ?? throw new IOException("无法确定安装目录");
        if (!Path.GetFileName(app).Equals("app", StringComparison.OrdinalIgnoreCase))
            throw new IOException("请从完整 MaaBanG 安装包启动后再更新");
        var start = new ProcessStartInfo(Path.Combine(app, "tools", "MaaBanGUpdater.exe"))
        {
            WorkingDirectory = root,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8
        };
        start.ArgumentList.Add("--prepare");
        start.ArgumentList.Add(Path.GetFullPath(archive));
        start.ArgumentList.Add(root);
        using var child = Process.Start(start) ?? throw new IOException("无法启动更新检查");
        var output = child.StandardOutput.ReadToEndAsync();
        var error = child.StandardError.ReadToEndAsync();
        await child.WaitForExitAsync();
        var work = (await output).Trim().TrimStart('\uFEFF');
        var message = await error;
        if (child.ExitCode != 0) throw new IOException(message);
        var plan = JObject.Parse(await File.ReadAllTextAsync(Path.Combine(work, "plan.json")));
        if (!string.IsNullOrEmpty(expectedVersion) && plan["version"]?.ToString() != expectedVersion)
            throw new IOException("更新包版本与发布版本不一致，已保留原安装");
        LoggerHelper.Info($"MaaBanG 整包校验通过，暂存目录：{work}");
        return work;
    }

    public static void ApplyAfterExit(string work)
    {
        using var current = Process.GetCurrentProcess();
        var start = new ProcessStartInfo(Path.Combine(work, "MaaBanGUpdater.exe"))
        {
            WorkingDirectory = work,
            UseShellExecute = false,
            CreateNoWindow = true
        };
        start.ArgumentList.Add("--apply");
        start.ArgumentList.Add(work);
        start.ArgumentList.Add(current.Id.ToString());
        start.ArgumentList.Add(current.StartTime.ToUniversalTime().Ticks.ToString());
        using var worker = Process.Start(start) ?? throw new IOException("无法启动整包更新程序");
    }
}
