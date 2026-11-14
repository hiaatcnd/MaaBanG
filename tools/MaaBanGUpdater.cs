using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Net;
using System.Net.Sockets;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Security.Principal;
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
    internal static void UpdateLog(string work, string message, object detail)
    {
        try
        {
            File.AppendAllText(Path.Combine(work, "update-events.jsonl"), Json.Serialize(new {
                time = DateTime.UtcNow.ToString("o"), message = message, detail = detail
            }) + Environment.NewLine, new UTF8Encoding(false));
        }
        catch (Exception error) { Console.Error.WriteLine("更新诊断日志写入失败：" + error.Message); }
    }
    static void Move(string source, string destination, string work, string phase, bool releaseAdb)
    {
        // A just-exited Agent or antivirus may hold a handle briefly.
        for (int attempt = 0; ; attempt++)
        {
            try
            {
                Directory.Move(source, destination);
                UpdateLog(work, "directory-moved", new { phase = phase, source = source, destination = destination, attempt = attempt });
                return;
            }
            catch (Exception error)
            {
                if (!(error is IOException) && !(error is UnauthorizedAccessException)) throw;
                if (attempt == 0)
                {
                    UpdateLog(work, "directory-move-blocked", new { phase = phase, source = source,
                        destination = destination, hresult = error.HResult, error = error.Message });
                    // Recover only the old installation's first rename. Never
                    // terminate arbitrary owners or interfere with rollback.
                    if (releaseAdb) AdbDirectoryLock.Release(source, work);
                }
                if (attempt == 39)
                    throw new IOException("无法移动安装目录（" + phase + "）：仍有程序占用该目录，或当前账户没有改名权限。" +
                        "请关闭占用该目录的程序后重试；更新器没有强制结束其他进程。目录：" + source, error);
                Thread.Sleep(250);
            }
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
        // Also protect direct CLI callers whose inherited cwd is inside target.
        // This is only the isolated helper's cwd, never the UI/Agent's global cwd.
        Directory.SetCurrentDirectory(work);
        UpdateLog(work, "apply-start", new { target = target, cwd = Environment.CurrentDirectory, uiPid = pid });
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
                Move(target, backup, work, "old-to-backup", true);
                moved = true;
                Move(stage, target, work, "stage-to-target", false);
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
                if (installed) Move(target, failed, work, "failed-new-to-staging", false);
                if (moved) Move(backup, target, work, "restore-backup", false);
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

// A detached adb server can keep the former UI cwd open after the UI exits.
// Only a positively identified local server holding this installation's cwd
// receives adb's normal host:kill request. There is deliberately no Kill fallback.
internal static class AdbDirectoryLock
{
    const uint ProcessRights = 0x00100000 | 0x0400 | 0x1000 | 0x0010 | 0x0040;
    const uint WaitTimeout = 258;
    const uint DuplicateSameAccess = 2;

    sealed class Identity
    {
        internal int Pid;
        internal long Created;
        internal string Image, Owner, Cwd, Command;
        internal int Port;
        internal object Evidence()
        {
            return new { pid = Pid, createdFileTime = Created, image = Image, owner = Owner,
                cwd = Cwd, command = Command, endpoint = "127.0.0.1:" + Port };
        }
    }
    [StructLayout(LayoutKind.Sequential)]
    struct ProcessBasicInformation
    {
        internal IntPtr Reserved1, Peb, Reserved2A, Reserved2B, Pid, Reserved3;
    }
    [DllImport("kernel32.dll", SetLastError = true)] static extern IntPtr OpenProcess(uint rights, bool inherit, int pid);
    [DllImport("kernel32.dll")] static extern IntPtr GetCurrentProcess();
    [DllImport("kernel32.dll", SetLastError = true)] static extern bool CloseHandle(IntPtr handle);
    [DllImport("kernel32.dll", SetLastError = true)] static extern bool IsWow64Process(IntPtr process, out bool wow64);
    [DllImport("kernel32.dll", SetLastError = true)] static extern uint WaitForSingleObject(IntPtr handle, uint milliseconds);
    [DllImport("kernel32.dll", SetLastError = true)] static extern bool GetProcessTimes(IntPtr process, out long created, out long exited, out long kernel, out long user);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] static extern bool QueryFullProcessImageName(IntPtr process, int flags, StringBuilder path, ref int size);
    [DllImport("kernel32.dll", SetLastError = true)] static extern bool ReadProcessMemory(IntPtr process, IntPtr address, byte[] data, IntPtr size, out IntPtr read);
    [DllImport("kernel32.dll", SetLastError = true)] static extern bool DuplicateHandle(IntPtr sourceProcess, IntPtr source, IntPtr targetProcess, out IntPtr target, uint access, bool inherit, uint options);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] static extern uint GetFinalPathNameByHandle(IntPtr handle, StringBuilder path, uint size, uint flags);
    [DllImport("ntdll.dll")] static extern int NtQueryInformationProcess(IntPtr process, int informationClass, ref ProcessBasicInformation information, int size, out int returned);
    [DllImport("ntdll.dll", EntryPoint = "NtQueryInformationProcess")] static extern int QueryWow64Peb(IntPtr process, int informationClass, out IntPtr information, int size, out int returned);
    [DllImport("advapi32.dll", SetLastError = true)] static extern bool OpenProcessToken(IntPtr process, uint access, out IntPtr token);
    [DllImport("advapi32.dll", SetLastError = true)] static extern bool GetTokenInformation(IntPtr token, int informationClass, IntPtr information, int size, out int returned);
    [DllImport("shell32.dll", CharSet = CharSet.Unicode, SetLastError = true)] static extern IntPtr CommandLineToArgvW(string command, out int count);
    [DllImport("kernel32.dll")] static extern IntPtr LocalFree(IntPtr value);
    [DllImport("iphlpapi.dll", SetLastError = true)] static extern uint GetExtendedTcpTable(IntPtr table, ref int size, bool order, int family, int tableClass, uint reserved);

    static void Check(bool ok) { if (!ok) throw new Win32Exception(Marshal.GetLastWin32Error()); }
    static string Canonical(string path)
    {
        if (path.StartsWith(@"\\?\UNC\", StringComparison.OrdinalIgnoreCase)) path = @"\\" + path.Substring(8);
        else if (path.StartsWith(@"\\?\", StringComparison.OrdinalIgnoreCase)) path = path.Substring(4);
        return Path.GetFullPath(path).TrimEnd('\\');
    }
    static bool Within(string path, string target)
    {
        return path.Equals(target, StringComparison.OrdinalIgnoreCase) ||
            path.StartsWith(target + "\\", StringComparison.OrdinalIgnoreCase);
    }
    static byte[] Read(IntPtr process, long address, int size)
    {
        if (address <= 0 || size < 0 || size > 65536) throw new IOException("无效的进程参数地址或长度");
        var data = new byte[size];
        IntPtr read;
        Check(ReadProcessMemory(process, new IntPtr(address), data, new IntPtr(size), out read));
        if (read.ToInt64() != size) throw new IOException("进程参数读取不完整");
        return data;
    }
    static long Pointer(IntPtr process, long address, int width)
    {
        var data = Read(process, address, width);
        return width == 4 ? BitConverter.ToUInt32(data, 0) : BitConverter.ToInt64(data, 0);
    }
    static string Unicode(IntPtr process, long address, int width)
    {
        var text = Read(process, address, width == 4 ? 8 : 16);
        var size = BitConverter.ToUInt16(text, 0);
        if (size == 0 || size % 2 != 0 || size > 32766) throw new IOException("无法读取进程路径或命令行");
        var buffer = width == 4 ? BitConverter.ToUInt32(text, 4) : BitConverter.ToInt64(text, 8);
        return Encoding.Unicode.GetString(Read(process, buffer, size));
    }
    static string Owner(IntPtr process)
    {
        IntPtr token;
        Check(OpenProcessToken(process, 8, out token));
        try
        {
            int size;
            GetTokenInformation(token, 1, IntPtr.Zero, 0, out size);
            if (size <= 0 || size > 65536) throw new IOException("无法确定进程用户");
            var data = Marshal.AllocHGlobal(size);
            try
            {
                Check(GetTokenInformation(token, 1, data, size, out size));
                return new SecurityIdentifier(Marshal.ReadIntPtr(data)).Value;
            }
            finally { Marshal.FreeHGlobal(data); }
        }
        finally { CloseHandle(token); }
    }
    static Identity Inspect(IntPtr process, int pid)
    {
        if (WaitForSingleObject(process, 0) != WaitTimeout) throw new IOException("进程已退出或身份无法确认");
        long created, exited, kernel, user;
        Check(GetProcessTimes(process, out created, out exited, out kernel, out user));
        var image = new StringBuilder(32768);
        int length = image.Capacity;
        Check(QueryFullProcessImageName(process, 0, image, ref length));
        var owner = Owner(process);
        using (var current = WindowsIdentity.GetCurrent())
            if (current.User == null || !String.Equals(owner, current.User.Value, StringComparison.Ordinal))
                throw new IOException("ADB 属于其他用户，不自动退出");
        if (!Path.GetFileName(image.ToString()).Equals("adb.exe", StringComparison.OrdinalIgnoreCase))
            throw new IOException("不是 adb.exe，不自动退出");
        bool wow64;
        Check(IsWow64Process(process, out wow64));
        if (Environment.Is64BitOperatingSystem && !Environment.Is64BitProcess && !wow64)
            throw new IOException("需要 64 位更新器才能安全检查此 64 位 ADB");
        int width = Environment.Is64BitOperatingSystem && !wow64 ? 8 : 4;
        long peb;
        int returned;
        if (wow64 && Environment.Is64BitProcess)
        {
            IntPtr address;
            if (QueryWow64Peb(process, 26, out address, IntPtr.Size, out returned) != 0)
                throw new IOException("无法读取 32 位 ADB 的进程信息");
            peb = address.ToInt64();
        }
        else
        {
            var basic = new ProcessBasicInformation();
            if (NtQueryInformationProcess(process, 0, ref basic, Marshal.SizeOf(basic), out returned) != 0)
                throw new IOException("无法读取 ADB 的进程信息");
            peb = basic.Peb.ToInt64();
        }
        long parameters = Pointer(process, peb + (width == 4 ? 0x10 : 0x20), width);
        string cwd = Canonical(Unicode(process, parameters + (width == 4 ? 0x24 : 0x38), width));
        string command = Unicode(process, parameters + (width == 4 ? 0x40 : 0x70), width);
        long cwdHandle = Pointer(process, parameters + (width == 4 ? 0x2c : 0x48), width);
        IntPtr copy;
        Check(DuplicateHandle(process, new IntPtr(cwdHandle), GetCurrentProcess(), out copy, 0, false, DuplicateSameAccess));
        try
        {
            var final = new StringBuilder(32768);
            uint size = GetFinalPathNameByHandle(copy, final, (uint)final.Capacity, 0);
            Check(size != 0);
            if (size >= final.Capacity || !Canonical(final.ToString()).Equals(cwd, StringComparison.OrdinalIgnoreCase))
                throw new IOException("ADB 当前目录与实际目录句柄不一致，不自动退出");
        }
        finally { CloseHandle(copy); } // Do not become another directory-lock owner.
        return new Identity { Pid = pid, Created = created, Image = Canonical(image.ToString()),
            Owner = owner, Cwd = cwd, Command = command };
    }
    static int ServerPort(string command)
    {
        int count;
        IntPtr argv = CommandLineToArgvW(command, out count);
        Check(argv != IntPtr.Zero);
        try
        {
            var args = Enumerable.Range(0, count).Select(i => Marshal.PtrToStringUni(Marshal.ReadIntPtr(argv, i * IntPtr.Size))).ToArray();
            // This is adb's own forked-server command, not an adb shell/client.
            if (count != 7 || args[1] != "-L" || args[3] != "fork-server" || args[4] != "server" || args[5] != "--reply-fd")
                throw new IOException("不是可确认的本机 ADB server 命令行，不自动退出");
            long reply;
            if (!Int64.TryParse(args[6], out reply) || reply < 0) throw new IOException("无效的 ADB server 参数");
            var match = System.Text.RegularExpressions.Regex.Match(args[2], @"^tcp:(?:(?:localhost|127\.0\.0\.1):)?([0-9]{1,5})$");
            int port;
            if (!match.Success || !Int32.TryParse(match.Groups[1].Value, out port) || port < 1 || port > 65535)
                throw new IOException("ADB 不是受支持的本机 TCP endpoint，不自动退出");
            return port;
        }
        finally { LocalFree(argv); }
    }
    static bool OwnsListener(int pid, int port)
    {
        int size = 0;
        uint result = GetExtendedTcpTable(IntPtr.Zero, ref size, false, 2, 3, 0); // IPv4, OWNER_PID_LISTENER
        if (result != 122 || size <= 0 || size > 16 * 1024 * 1024) return false;
        var table = Marshal.AllocHGlobal(size);
        try
        {
            if (GetExtendedTcpTable(table, ref size, false, 2, 3, 0) != 0) return false;
            int count = Marshal.ReadInt32(table);
            if (count < 0 || 4L + count * 24L > size) return false;
            for (int i = 0; i < count; i++)
            {
                var row = IntPtr.Add(table, 4 + i * 24);
                int localPort = (Marshal.ReadByte(row, 8) << 8) | Marshal.ReadByte(row, 9);
                // Deliberately refuse wildcard/remote/forwarded endpoints.
                bool loopback = Marshal.ReadByte(row, 4) == 127 && Marshal.ReadByte(row, 5) == 0 &&
                    Marshal.ReadByte(row, 6) == 0 && Marshal.ReadByte(row, 7) == 1;
                if (Marshal.ReadInt32(row) == 2 && loopback && localPort == port && Marshal.ReadInt32(row, 20) == pid)
                    return true;
            }
            return false;
        }
        finally { Marshal.FreeHGlobal(table); }
    }
    static void Recheck(IntPtr process, Identity original, string target)
    {
        var now = Inspect(process, original.Pid);
        if (now.Created != original.Created || now.Image != original.Image || now.Owner != original.Owner ||
            now.Cwd != original.Cwd || now.Command != original.Command || !Within(now.Cwd, target) ||
            ServerPort(now.Command) != original.Port || !OwnsListener(original.Pid, original.Port))
            throw new IOException("ADB 身份、当前目录或监听归属已改变，不发送退出请求");
    }
    static int Remaining(Stopwatch budget, int maximum)
    {
        int remaining = (int)Math.Min(maximum, 8000 - budget.ElapsedMilliseconds);
        if (remaining <= 0) throw new IOException("ADB 占用检查已超时，未强制结束进程");
        return remaining;
    }
    static void Stop(IntPtr process, Identity identity, string target, string work, Stopwatch budget)
    {
        Recheck(process, identity, target);
        using (var client = new TcpClient(AddressFamily.InterNetwork))
        {
            var connect = client.BeginConnect(IPAddress.Loopback, identity.Port, null, null);
            using (var ready = connect.AsyncWaitHandle)
                if (!ready.WaitOne(Remaining(budget, 1500))) throw new IOException("连接指定 ADB server 超时");
            client.EndConnect(connect);
            // Keep the original process handle throughout: no PID lookup or adb
            // subprocess can accidentally address a replacement server here.
            Recheck(process, identity, target);
            using (var stream = client.GetStream())
            {
                stream.WriteTimeout = Remaining(budget, 1500);
                var request = Encoding.ASCII.GetBytes("0009host:kill");
                UpdateLog(work, "adb-graceful-exit-request", identity.Evidence());
                stream.Write(request, 0, request.Length);
                var status = new byte[4];
                int offset = 0;
                while (offset < status.Length)
                {
                    stream.ReadTimeout = Remaining(budget, 1500);
                    int read = stream.Read(status, offset, status.Length - offset);
                    if (read == 0) break;
                    offset += read;
                }
                if (offset != 4 || Encoding.ASCII.GetString(status) != "OKAY")
                    throw new IOException("指定 ADB server 未接受正常退出请求，未强制结束进程");
            }
        }
        if (WaitForSingleObject(process, (uint)Remaining(budget, 3000)) != 0)
            throw new IOException("指定 ADB server 未按时正常退出，未强制结束进程");
        UpdateLog(work, "adb-exited", identity.Evidence());
    }
    static void UpdateLog(string work, string message, object detail) { MaaBanGUpdater.UpdateLog(work, message, detail); }
    internal static void Release(string target, string work)
    {
        target = Canonical(target);
        var budget = Stopwatch.StartNew();
        Process[] candidates;
        try { candidates = Process.GetProcessesByName("adb"); }
        catch (Exception error)
        {
            UpdateLog(work, "adb-scan-unavailable", new { error = error.Message });
            return;
        }
        try
        {
            foreach (var candidate in candidates)
            {
                if (budget.ElapsedMilliseconds >= 8000) break;
                IntPtr process = IntPtr.Zero;
                int pid = candidate.Id;
                try
                {
                    process = OpenProcess(ProcessRights, false, pid);
                    Check(process != IntPtr.Zero);
                    var identity = Inspect(process, pid);
                    if (!Within(identity.Cwd, target)) continue;
                    identity.Port = ServerPort(identity.Command);
                    if (!OwnsListener(pid, identity.Port)) throw new IOException("监听端口不属于此 ADB，不发送退出请求");
                    UpdateLog(work, "adb-cwd-lock-confirmed", identity.Evidence());
                    Stop(process, identity, target, work, budget);
                }
                catch (Exception error)
                {
                    UpdateLog(work, "adb-lock-not-released", new { pid = pid, error = error.Message });
                }
                finally { if (process != IntPtr.Zero) CloseHandle(process); }
            }
        }
        finally { foreach (var candidate in candidates) candidate.Dispose(); }
    }
}
