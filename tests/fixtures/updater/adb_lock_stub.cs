// A local process fixture only: its private loopback socket never accesses ADB or devices.
using System;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;

internal static class AdbLockStub
{
    private static bool ReadKill(TcpClient client, string marker)
    {
        using (client)
        using (var stream = client.GetStream())
        {
            stream.ReadTimeout = stream.WriteTimeout = 1000;
            var bytes = new byte[13];
            int count = 0;
            try
            {
                while (count < bytes.Length)
                {
                    int read = stream.Read(bytes, count, bytes.Length - count);
                    if (read == 0) return false;
                    count += read;
                }
                var request = Encoding.ASCII.GetString(bytes);
                File.WriteAllText(marker, request);
                if (request != "0009host:kill") return false;
                var reply = Encoding.ASCII.GetBytes("OKAY");
                stream.Write(reply, 0, reply.Length);
                return true;
            }
            catch (IOException) { return false; }
        }
    }

    private static int Main()
    {
        string ready = Environment.GetEnvironmentVariable("MAABANG_TEST_STUB_READY");
        string stop = Environment.GetEnvironmentVariable("MAABANG_TEST_STUB_STOP");
        string marker = Environment.GetEnvironmentVariable("MAABANG_TEST_STUB_KILL_MARKER");
        string connected = Environment.GetEnvironmentVariable("MAABANG_TEST_STUB_CONNECT_MARKER");
        if (String.IsNullOrEmpty(ready) || String.IsNullOrEmpty(stop)) return 2;
        bool ignoreKill = Environment.GetEnvironmentVariable("MAABANG_TEST_STUB_IGNORE_KILL") == "1";
        int port = Int32.Parse(Environment.GetEnvironmentVariable("MAABANG_TEST_STUB_PORT") ?? "0");
        TcpListener listener = port == 0 ? null : new TcpListener(IPAddress.Loopback, port);
        try
        {
            if (listener != null) listener.Start();
            File.WriteAllText(ready + ".tmp", IntPtr.Size.ToString() + "\n" + Directory.GetCurrentDirectory());
            File.Move(ready + ".tmp", ready);
            // A hard upper bound prevents a failed runner leaving a permanent lock.
            DateTime deadline = DateTime.UtcNow.AddSeconds(90);
            while (!File.Exists(stop) && DateTime.UtcNow < deadline)
            {
                if (listener != null && listener.Pending())
                {
                    var client = listener.AcceptTcpClient();
                    File.WriteAllText(connected, "connected");
                    if (ReadKill(client, marker) && !ignoreKill) return 0;
                }
                Thread.Sleep(25);
            }
        }
        finally { if (listener != null) listener.Stop(); }
        return 0;
    }
}
