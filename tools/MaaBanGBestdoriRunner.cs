using System;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using MaaFramework.Binding;
using MFAAvalonia.Helper;
using Newtonsoft.Json.Linq;

namespace MFAAvalonia.Extensions.MaaFW;

public partial class MaaProcessor
{
    private int _bestdoriToolActive;
    private TaskCompletionSource? _bestdoriToolStopped;
    public bool IsBestdoriToolActive => Volatile.Read(ref _bestdoriToolActive) != 0;
    private void CancelBestdoriScan(Action? afterStopped)
    {
        var stopped = _bestdoriToolStopped?.Task ?? Task.CompletedTask;
        CancellationTokenSource?.Cancel();
        if (afterStopped != null)
            _ = stopped.ContinueWith(_ => DispatcherHelper.PostOnMainThread(afterStopped), TaskScheduler.Default);
    }
    public async Task RunBestdoriScanAsync(string scanId, CancellationToken cancellation)
    {
        if (_isClosed || IsConnecting || TaskQueue.Count > 0 || !TryBeginTaskRun())
            throw new InvalidOperationException("当前设备正在使用中，请等待结束后再读取");
        CancellationTokenSource = CancellationTokenSource.CreateLinkedTokenSource(cancellation);
        var stopped = _bestdoriToolStopped = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        Interlocked.Exchange(ref _bestdoriToolActive, 1);
        DispatcherHelper.PostOnMainThread(() => Instances.RootViewModel.IsRunning = true);
        var token = CancellationTokenSource.Token;
        try
        {
            // Only initialize the selected device/resource/Agent. Do not run
            // daily tasks, startup scripts, emulator launch or after-task actions.
            var tasker = await Task.Run(() => GetTaskerAsync(token), token)
                ?? throw new InvalidOperationException("无法连接设备，请先在主页选择已启动的模拟器");
            token.ThrowIfCancellationRequested();
            if (tasker.IsRunning || tasker.IsStopping)
                throw new InvalidOperationException("设备仍有操作尚未结束");
            var parameters = new JObject { ["BestdoriProfiles"] = new JObject
            { ["custom_action_param"] = new JObject { ["scan_id"] = scanId } } };
            // Keep the reservation until the job actually exits, including after
            // cancellation. A canceled wait must not unlock a still-running scan.
            var job = tasker.AppendTask("BestdoriProfiles", parameters.ToString());
            using var cancel = token.Register(() => tasker.Stop());
            var status = await Task.Run(() => job.Wait());
            token.ThrowIfCancellationRequested();
            if (status != MaaJobStatus.Succeeded)
                throw new InvalidOperationException("读取未全部完成，请检查工具页保留的记录");
        }
        finally
        {
            Interlocked.Exchange(ref _bestdoriToolActive, 0);
            EndTaskRun();
            DispatcherHelper.PostOnMainThread(() => Instances.RootViewModel.IsRunning = Processors.Any(p => p.TaskQueue.Count > 0 || Volatile.Read(ref p._bestdoriToolActive) != 0));
            stopped.TrySetResult();
        }
    }
}
