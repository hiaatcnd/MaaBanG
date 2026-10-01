using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Forms;

// Runs from a sibling staging directory, so neither the UI nor its Python/DLLs
// are loaded from the directory being replaced. Never deletes the old package.
internal static class MaaBanGUpdater
{
    static readonly JavaScriptSerializer Json = new JavaScriptSerializer { MaxJsonLength = 16 * 1024 * 1024 };
    static readonly string[] Required = {
        "MaaBanG.exe", "app/interface.json", "app/MFAAvalonia.exe",
        "app/libs/MFAAvalonia.Core.dll", "app/python/python.exe",
        "app/agent/main.py", "app/agent/user_data.py", "app/tools/package_smoke.py",
        "app/tools/MaaBanGUpdater.exe", "app/tools/MaaBanGUpdater.exe.config", "app/resource/model/ocr/rec.onnx"
    };
    public class Manifest
    {
        public int format { get; set; }
        public string name { get; set; }
        public string version { get; set; }
        public string platform { get; set; }
        public Dictionary<string, string> files { get; set; }
    }
    public class Plan
    {
        public string target { get; set; }
        public string interfaceHash { get; set; }
        public string version { get; set; }
    }
    static string Hash(string path)
    {
        using (var algorithm = SHA256.Create())
        using (var file = File.OpenRead(path))
            return BitConverter.ToString(algorithm.ComputeHash(file)).Replace("-", "").ToLowerInvariant();
    }
    static string Extended(string path)
    {
        var full = Path.GetFullPath(path);
        if (full.StartsWith(@"\\?\")) return full;
        return full.StartsWith(@"\\") ? @"\\?\UNC\" + full.Substring(2) : @"\\?\" + full;
    }
    static bool Under(string path, string parent)
    {
        return path.Equals(parent, StringComparison.OrdinalIgnoreCase) ||
            path.StartsWith(parent.TrimEnd('\\') + "\\", StringComparison.OrdinalIgnoreCase);
    }
    static void NoLinks(string path)
    {
        for (var current = new DirectoryInfo(path); current != null; current = current.Parent)
            if (current.Exists && (current.Attributes & FileAttributes.ReparsePoint) != 0)
                throw new IOException("更新路径不能包含链接或目录联接：" + current.FullName);
    }
    static string SafePath(string root, string relative)
    {
        if (String.IsNullOrEmpty(relative) || relative.Contains('\\') || relative.StartsWith("/"))
            throw new InvalidDataException("更新包路径无效：" + relative);
        foreach (var part in relative.Split('/'))
        {
            var stem = part.Split('.')[0].ToUpperInvariant();
            if (part.Length == 0 || part == "." || part == ".." || part.EndsWith(".") || part.EndsWith(" ") ||
                part.IndexOfAny(Path.GetInvalidFileNameChars()) >= 0 ||
                new[] { "CON", "PRN", "AUX", "NUL", "CLOCK$" }.Contains(stem) ||
                System.Text.RegularExpressions.Regex.IsMatch(stem, @"^(COM|LPT)[0-9]$"))
                throw new InvalidDataException("更新包路径无效：" + relative);
        }
        var path = Path.GetFullPath(Path.Combine(root, relative.Replace('/', '\\')));
        if (!Under(path, root) || path.Equals(root, StringComparison.OrdinalIgnoreCase))
            throw new InvalidDataException("更新包路径越界");
        return path;
    }
    static Dictionary<string, object> Interface(string root)
    {
        var value = Json.Deserialize<Dictionary<string, object>>(File.ReadAllText(Path.Combine(root, "app", "interface.json")));
        if (!value.ContainsKey("name") || (string)value["name"] != "MaaBanG")
            throw new InvalidDataException("此安装包不属于 MaaBanG");
        return value;
    }
    static void Target(string root)
    {
        NoLinks(root);
        if (Directory.GetParent(root) == null || !File.Exists(Path.Combine(root, "MaaBanG.exe")))
            throw new InvalidDataException("不是完整的 MaaBanG 安装目录");
        Interface(root);
        var data = Environment.GetEnvironmentVariable("MAABANG_DATA_DIR");
        if (String.IsNullOrWhiteSpace(data))
            data = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".maabang");
        if (Under(Path.GetFullPath(data), root))
            throw new IOException("用户数据目录位于安装目录内，请先将 MAABANG_DATA_DIR 移到安装目录之外。");
    }
    static Manifest Validate(string root)
    {
        root = Extended(root);
        NoLinks(root);
        var manifest = Json.Deserialize<Manifest>(File.ReadAllText(Path.Combine(root, "package-manifest.json")));
        if (manifest.format != 1 || manifest.name != "MaaBanG" || manifest.platform != "win-x64" || manifest.files == null ||
            !System.Text.RegularExpressions.Regex.IsMatch(manifest.version ?? "", @"^v\d+\.\d+\.\d+(?:-[\w.-]+)?$"))
            throw new InvalidDataException("不支持的 MaaBanG 更新包清单");
        var expected = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var item in manifest.files)
        {
            var file = SafePath(root, item.Key);
            if (!expected.Add(file) || !File.Exists(file) || Hash(file) != item.Value)
                throw new InvalidDataException("更新包文件校验失败：" + item.Key);
        }
        foreach (var file in Required)
            if (!expected.Contains(SafePath(root, file)))
                throw new InvalidDataException("更新包缺少必需文件：" + file);
        foreach (var dir in Directory.GetDirectories(root, "*", SearchOption.AllDirectories)) NoLinks(dir);
        foreach (var file in Directory.GetFiles(root, "*", SearchOption.AllDirectories))
        {
            if ((File.GetAttributes(file) & FileAttributes.ReparsePoint) != 0)
                throw new InvalidDataException("更新包不能包含链接");
            if (!file.Equals(Path.Combine(root, "package-manifest.json"), StringComparison.OrdinalIgnoreCase) && !expected.Contains(file))
                throw new InvalidDataException("更新包包含清单外文件：" + file);
        }
        if ((string)Interface(root)["version"] != manifest.version)
            throw new InvalidDataException("更新包版本与清单不一致");
        return manifest;
    }
    static string Prepare(string archive, string target)
    {
        target = Path.GetFullPath(target).TrimEnd('\\');
        Target(target);
        var work = Path.Combine(Directory.GetParent(target).FullName, ".maabang-update-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(work);
        var stage = Extended(Path.Combine(work, "next"));
        Directory.CreateDirectory(stage);
        var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        string prefix = null;
        long total = 0;
        using (var zip = ZipFile.OpenRead(archive))
        {
            if (zip.Entries.Count > 30000) throw new InvalidDataException("更新包文件过多");
            foreach (var entry in zip.Entries)
            {
                var name = entry.FullName.TrimEnd('/');
                SafePath(work, name);
                var split = name.IndexOf('/');
                var top = split < 0 ? name : name.Substring(0, split);
                if (prefix == null) prefix = top;
                if (prefix != top || !top.StartsWith("MaaBanG-", StringComparison.Ordinal))
                    throw new InvalidDataException("请使用完整的 MaaBanG Windows 安装包");
                if (split < 0)
                {
                    if (!entry.FullName.EndsWith("/")) throw new InvalidDataException("更新包目录结构无效");
                    continue;
                }
                var destination = SafePath(stage, name.Substring(split + 1));
                if (!seen.Add(destination) || ((entry.ExternalAttributes >> 16) & 0xF000) == 0xA000)
                    throw new InvalidDataException("更新包包含重复路径或链接");
                if (entry.FullName.EndsWith("/")) { Directory.CreateDirectory(destination); continue; }
                total = checked(total + entry.Length);
                if (total > 4L * 1024 * 1024 * 1024) throw new InvalidDataException("更新包过大");
                Directory.CreateDirectory(Path.GetDirectoryName(destination));
                entry.ExtractToFile(destination);
            }
        }
        var manifest = Validate(stage);
        var plan = new Plan { target = target, interfaceHash = Hash(Path.Combine(target, "app", "interface.json")), version = manifest.version };
        File.Copy(System.Reflection.Assembly.GetExecutingAssembly().Location, Path.Combine(work, "MaaBanGUpdater.exe"));
        File.Copy(System.Reflection.Assembly.GetExecutingAssembly().Location + ".config", Path.Combine(work, "MaaBanGUpdater.exe.config"));
        File.WriteAllText(Path.Combine(work, "plan.json"), Json.Serialize(plan), Encoding.UTF8);
        return work;
    }
    static void Move(string source, string destination)
    {
        // A just-exited Agent or antivirus may hold a handle briefly.
        for (int attempt = 0; ; attempt++)
        {
            try { Directory.Move(source, destination); return; }
            catch (IOException) { if (attempt == 39) throw; Thread.Sleep(250); }
            catch (UnauthorizedAccessException) { if (attempt == 39) throw; Thread.Sleep(250); }
        }
    }
    static void Start(string root)
    {
        Process.Start(new ProcessStartInfo(Path.Combine(root, "MaaBanG.exe")) { WorkingDirectory = root, UseShellExecute = false });
    }
    static void Apply(string work, int pid, long started, bool restart)
    {
        work = Path.GetFullPath(work).TrimEnd('\\');
        NoLinks(work);
        var plan = Json.Deserialize<Plan>(File.ReadAllText(Path.Combine(work, "plan.json")));
        var target = Path.GetFullPath(plan.target).TrimEnd('\\');
        var parent = Directory.GetParent(target).FullName;
        if (Directory.GetParent(work).FullName != parent || !Path.GetFileName(work).StartsWith(".maabang-update-"))
            throw new InvalidDataException("更新暂存目录与目标目录不匹配");
        var stage = Path.Combine(work, "next");
        var backup = Path.Combine(work, "previous");
        var failed = Path.Combine(work, "failed");
        var lockPath = Path.Combine(parent, "." + Path.GetFileName(target) + ".update.lock");
        using (var updateLock = new FileStream(lockPath, FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None))
        {
            Target(target);
            if (Directory.Exists(backup) || Directory.Exists(failed)) throw new IOException("此更新已经执行过");
            if (Validate(stage).version != plan.version) throw new InvalidDataException("暂存版本已改变");
            if (pid > 0)
            {
                Process previous = null;
                try { previous = Process.GetProcessById(pid); } catch (ArgumentException) { }
                if (previous != null)
                    using (previous)
                        if (previous.StartTime.ToUniversalTime().Ticks == started && !previous.WaitForExit(120000))
                            throw new TimeoutException("MaaBanG 尚未退出，已取消更新，旧版本保持不变。");
            }
            if (Hash(Path.Combine(target, "app", "interface.json")) != plan.interfaceHash)
                throw new IOException("安装版本已经改变，请重新检查更新");
            var moved = false;
            var installed = false;
            try
            {
                Move(target, backup);
                moved = true;
                Move(stage, target);
                installed = true;
                // Verifies embedded Python, framework parity, OCR and Agent IPC.
                using (var check = Process.Start(new ProcessStartInfo(Path.Combine(target, "MaaBanG.exe"), "--check-agent") {
                    WorkingDirectory = target, UseShellExecute = false, CreateNoWindow = true }))
                {
                    if (!check.WaitForExit(90000)) { check.Kill(); check.WaitForExit(); throw new IOException("新版启动检查超时"); }
                    if (check.ExitCode != 0) throw new IOException("新版启动检查未通过");
                }
                if (restart) Start(target);
            }
            catch
            {
                if (installed) Move(target, failed);
                if (moved) Move(backup, target);
                File.WriteAllText(Path.Combine(work, "status.txt"), "rolled-back", Encoding.UTF8);
                if (restart && moved) Start(target);
                throw;
            }
            File.WriteAllText(Path.Combine(work, "status.txt"), "complete", Encoding.UTF8);
        }
    }
    [STAThread]
    static int Main(string[] args)
    {
        bool apply = args.Length >= 1 && args[0] == "--apply";
        bool restart = !args.Contains("--no-restart");
        try
        {
            Console.OutputEncoding = new UTF8Encoding(false);
            Console.SetError(new StreamWriter(Console.OpenStandardError(), new UTF8Encoding(false)) { AutoFlush = true });
            if (args.Length == 3 && args[0] == "--prepare")
                Console.WriteLine(Prepare(Path.GetFullPath(args[1]), args[2]));
            else if (apply && (args.Length == 4 || (args.Length == 5 && !restart)))
                Apply(args[1], Int32.Parse(args[2]), Int64.Parse(args[3]), restart);
            else throw new ArgumentException("用法：--prepare ZIP 安装目录；--apply 暂存目录 PID 进程启动时间 [--no-restart]");
            return 0;
        }
        catch (Exception error)
        {
            Console.Error.WriteLine(error.Message);
            if (apply)
            {
                try { File.WriteAllText(Path.Combine(args[1], "update-error.log"), error.ToString(), Encoding.UTF8); } catch { }
                if (restart) MessageBox.Show("MaaBanG 更新未完成。请查看暂存目录中的 update-error.log。\n" + error.Message, "MaaBanG 更新");
            }
            return 1;
        }
    }
}
