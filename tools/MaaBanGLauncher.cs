using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Windows.Forms;

// Keep the workaround in the parent process: MaaAgentClient is created by the UI,
// before the Python Agent starts. Changing TEMP only in Python is too late.
internal static class MaaBanGLauncher
{
    [STAThread]
    private static int Main(string[] args)
    {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string app = Path.Combine(root, "app");
        bool check = args.Length == 1 && args[0] == "--check-agent";
        try
        {
            // libzmq 4.3.5 can bind but fail to connect AF_UNIX sockets under
            // AppData/Temp on Windows (zeromq/libzmq#4734). Use a writable,
            // per-user directory outside AppData for this process tree only.
            string configuredData = Environment.GetEnvironmentVariable("MAABANG_DATA_DIR");
            string data = check ? Path.Combine(app, "debug", "smoke-user-data") :
                string.IsNullOrWhiteSpace(configuredData) ?
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".maabang") :
                Path.GetFullPath(configuredData);
            // IPC keeps its short, proven path even for a custom data directory.
            string temp = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".maabang", "temp");
            Directory.CreateDirectory(temp);
            var start = new ProcessStartInfo
            {
                FileName = Path.Combine(app, check ? "python\\python.exe" : "MFAAvalonia.exe"),
                Arguments = check ? "\"" + Path.Combine(app, "tools\\package_smoke.py") + "\" \"" + app.TrimEnd('\\') + "\"" : "",
                WorkingDirectory = app,
                UseShellExecute = false,
                CreateNoWindow = check
            };
            start.EnvironmentVariables["TEMP"] = temp;
            start.EnvironmentVariables["TMP"] = temp;
            start.EnvironmentVariables["PYTHONUTF8"] = "1";
            start.EnvironmentVariables["MAABANG_DATA_DIR"] = data;
            if (!check)
            {
                var migration = new ProcessStartInfo
                {
                    FileName = Path.Combine(app, "python\\python.exe"),
                    Arguments = "-X utf8 \"" + Path.Combine(app, "agent\\user_data.py") + "\" --migrate \"" + app.TrimEnd('\\') + "\"",
                    WorkingDirectory = app,
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    RedirectStandardOutput = true,
                    RedirectStandardError = true
                };
                migration.EnvironmentVariables["MAABANG_DATA_DIR"] = data;
                using (var child = Process.Start(migration))
                {
                    if (!child.WaitForExit(120000))
                    {
                        child.Kill();
                        throw new IOException("用户数据迁移超时，原配置仍保留在旧目录。");
                    }
                    if (child.ExitCode != 0)
                        throw new IOException("用户数据迁移失败：" + child.StandardError.ReadToEnd());
                }
            }
            using (var child = Process.Start(start))
            {
                if (check)
                {
                    if (!child.WaitForExit(60000))
                    {
                        child.Kill();
                        return 2;
                    }
                    return child.ExitCode;
                }
            }
            return 0;
        }
        catch (Exception error)
        {
            try { File.WriteAllText(Path.Combine(root, "launcher-error.log"), error.ToString(), Encoding.UTF8); }
            catch { }
            if (!check) MessageBox.Show("启动失败，请检查解压目录是否完整且可写。\n" + error.Message, "MaaBanG");
            return 1;
        }
    }
}
