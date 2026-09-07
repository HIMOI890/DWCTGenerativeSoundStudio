using EdmgStudio.Core.Graphics;
using EdmgStudio.Core.Media;
using EdmgStudio.WinUI.Graphics;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Controls.Primitives;
using System.Diagnostics;
using System.Globalization;

namespace EdmgStudio.WinUI.Controls;

public sealed partial class Direct3DPreviewControl : UserControl
{
    private readonly ImageFrameDecoder _decoder = new();
    private readonly SemaphoreSlim _videoLifecycleGate = new(1, 1);
    private CancellationTokenSource? _loadCancellation;
    private CancellationTokenSource? _playbackCancellation;
    private CancellationTokenSource? _seekDebounceCancellation;
    private PreviewRendererSession? _renderer;
    private VideoPlaybackSession? _videoSession;
    private Task _videoCleanupTask = Task.CompletedTask;
    private XamlRoot? _subscribedXamlRoot;
    private string _emptyMessage = "No preview selected.";
    private bool _hasFrame;
    private bool _isLoading;
    private bool _isVideoPlaying;
    private bool _isUpdatingPosition;
    private bool _resumePlaybackAfterSeek;
    private int _videoGeneration;
    private TimeSpan _videoPosition;

    public Direct3DPreviewControl()
    {
        InitializeComponent();
    }

    public string? AdapterDiagnostics { get; private set; }

    public async Task LoadStreamAsync(
        Stream source,
        string? contentType,
        CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(source);
        CancellationTokenSource linkedCancellation = ReplaceLoadCancellation(cancellationToken);
        CancellationToken token = linkedCancellation.Token;
        await DisposeVideoSessionAsync();

        _isLoading = true;
        _hasFrame = false;
        await SetStateAsync("Loading preview…", isProgressActive: true, isVisible: true);

        OwnedCpuFrame? frame = null;
        try
        {
            frame = await _decoder.DecodeAsync(source, contentType, token).ConfigureAwait(false);
            token.ThrowIfCancellationRequested();

            PreviewRendererSession? renderer = Volatile.Read(ref _renderer);
            if (renderer is null)
            {
                throw new InvalidOperationException("The preview surface is not available.");
            }

            if (!renderer.TrySubmitFrame(frame))
            {
                frame = null;
                throw new InvalidOperationException("The preview renderer is stopping.");
            }

            frame = null;
            _isLoading = false;
            _hasFrame = true;
            await SetStateAsync(string.Empty, isProgressActive: false, isVisible: false);
        }
        catch (OperationCanceledException)
        {
            throw;
        }
        catch (PreviewUnsupportedException exception)
        {
            _isLoading = false;
            _hasFrame = false;
            await SetStateAsync(exception.Message, isProgressActive: false, isVisible: true);
        }
        catch (Exception exception)
        {
            _isLoading = false;
            _hasFrame = false;
            await SetStateAsync(
                $"Preview could not be displayed. {exception.Message}",
                isProgressActive: false,
                isVisible: true);
        }
        finally
        {
            frame?.Dispose();
            if (ReferenceEquals(
                Interlocked.CompareExchange(ref _loadCancellation, null, linkedCancellation),
                linkedCancellation))
            {
                linkedCancellation.Dispose();
            }
        }
    }

    public Task LoadVideoStreamAsync(Stream source, CancellationToken cancellationToken)
        => LoadVideoStreamAsync(source, knownContentLength: null, cancellationToken);

    public async Task LoadVideoStreamAsync(
        Stream source,
        long? knownContentLength,
        CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(source);
        CancellationTokenSource linkedCancellation = ReplaceLoadCancellation(cancellationToken);
        CancellationToken token = linkedCancellation.Token;
        await DisposeVideoSessionAsync();

        int generation = Interlocked.Increment(ref _videoGeneration);
        _isLoading = true;
        _hasFrame = false;
        await SetStateAsync("Preparing video preview…", isProgressActive: true, isVisible: true);

        VideoPlaybackSession? session = null;
        try
        {
            MediaToolPaths tools = MediaToolLocator.Locate();
            session = await VideoPlaybackSession.CreateAsync(source, tools, knownContentLength, token).ConfigureAwait(false);
            token.ThrowIfCancellationRequested();
            if (generation != Volatile.Read(ref _videoGeneration))
            {
                throw new OperationCanceledException(token);
            }

            if (!App.Services.TryTrackVideoPlaybackSession(session))
            {
                throw new InvalidOperationException("Video playback is unavailable while Studio is shutting down.");
            }

            _videoSession = session;
            session = null;
            _videoPosition = TimeSpan.Zero;
            _isLoading = false;
            await ConfigureVideoTransportAsync(_videoSession.Metadata);
            _ = StartPlaybackAsync(_videoSession, TimeSpan.Zero, generation);
        }
        catch (OperationCanceledException)
        {
            throw;
        }
        catch (Exception exception)
        {
            _isLoading = false;
            _hasFrame = false;
            await SetStateAsync(
                $"Video preview could not be displayed. {exception.Message}",
                isProgressActive: false,
                isVisible: true);
        }
        finally
        {
            if (session is not null)
            {
                await session.DisposeAsync();
            }

            if (ReferenceEquals(
                Interlocked.CompareExchange(ref _loadCancellation, null, linkedCancellation),
                linkedCancellation))
            {
                linkedCancellation.Dispose();
            }
        }
    }

    public void ShowEmpty(string message = "No preview selected.")
    {
        CancelPendingLoad();
        BeginVideoCleanup();
        _emptyMessage = message;
        _isLoading = false;
        _hasFrame = false;
        SetState(message, isProgressActive: false, isVisible: true);
    }

    public void ShowUnsupported(string message)
    {
        CancelPendingLoad();
        BeginVideoCleanup();
        _isLoading = false;
        _hasFrame = false;
        SetState(message, isProgressActive: false, isVisible: true);
    }

    public void ShowError(string message)
    {
        CancelPendingLoad();
        BeginVideoCleanup();
        _isLoading = false;
        _hasFrame = false;
        SetState(message, isProgressActive: false, isVisible: true);
    }

    private void Direct3DPreviewControl_Loaded(object sender, RoutedEventArgs e)
    {
        SubscribeToXamlRoot();
        if (_renderer is null)
        {
            var renderer = new PreviewRendererSession(
                PreviewPanel,
                DispatcherQueue,
                Renderer_StatusChanged,
                Renderer_DiagnosticsChanged);
            if (App.Services.TryTrackPreviewSession(renderer))
            {
                _renderer = renderer;
            }
            else
            {
                _ = renderer.DisposeAsync();
                ShowError("Preview is unavailable while Studio is shutting down.");
                return;
            }
        }

        RequestResize();
    }

    private async void Direct3DPreviewControl_Unloaded(object sender, RoutedEventArgs e)
    {
        CancelPendingLoad();
        UnsubscribeFromXamlRoot();
        try
        {
            await DisposeVideoSessionAsync();
        }
        catch (Exception exception)
        {
            Debug.WriteLine($"Video playback shutdown failed: {exception}");
        }

        PreviewRendererSession? renderer = Interlocked.Exchange(ref _renderer, null);
        if (renderer is null)
        {
            return;
        }

        App.Services.UntrackPreviewSession(renderer);
        try
        {
            await renderer.DisposeAsync();
        }
        catch (Exception exception)
        {
            Debug.WriteLine($"Preview renderer shutdown failed: {exception}");
        }
    }

    private void PreviewPanel_SizeChanged(object sender, SizeChangedEventArgs e) => RequestResize();

    private void XamlRoot_Changed(XamlRoot sender, XamlRootChangedEventArgs args) => RequestResize();

    private void SubscribeToXamlRoot()
    {
        XamlRoot? current = XamlRoot;
        if (ReferenceEquals(current, _subscribedXamlRoot))
        {
            return;
        }

        UnsubscribeFromXamlRoot();
        _subscribedXamlRoot = current;
        if (_subscribedXamlRoot is not null)
        {
            _subscribedXamlRoot.Changed += XamlRoot_Changed;
        }
    }

    private void UnsubscribeFromXamlRoot()
    {
        if (_subscribedXamlRoot is not null)
        {
            _subscribedXamlRoot.Changed -= XamlRoot_Changed;
            _subscribedXamlRoot = null;
        }
    }

    private void RequestResize()
    {
        SubscribeToXamlRoot();
        double scale = XamlRoot?.RasterizationScale ?? 1.0;
        _renderer?.RequestResize(PreviewPanel.ActualWidth, PreviewPanel.ActualHeight, scale);
    }

    private void Renderer_StatusChanged(RendererStatus status)
    {
        switch (status.State)
        {
            case RendererLifecycleState.Initializing:
                SetState(status.Message, isProgressActive: true, isVisible: true);
                break;
            case RendererLifecycleState.Ready:
                if (_hasFrame)
                {
                    SetState(string.Empty, isProgressActive: false, isVisible: false);
                }
                else if (_isLoading)
                {
                    SetState("Loading preview…", isProgressActive: true, isVisible: true);
                }
                else
                {
                    SetState(_emptyMessage, isProgressActive: false, isVisible: true);
                }

                break;
            case RendererLifecycleState.Recovering:
                SetState(status.Message, isProgressActive: true, isVisible: true);
                break;
            case RendererLifecycleState.Faulted:
                SetState(status.Message, isProgressActive: false, isVisible: true);
                break;
        }
    }

    private void Renderer_DiagnosticsChanged(PreviewAdapterDiagnostics diagnostics)
    {
        void Update()
        {
            AdapterDiagnostics =
                $"{diagnostics.Description}; LUID {diagnostics.LuidText}; " +
                (diagnostics.IsWarp ? "WARP" : "hardware");
            ToolTipService.SetToolTip(this, AdapterDiagnostics);
            AutomationProperties.SetHelpText(this, AdapterDiagnostics);
        }

        if (DispatcherQueue.HasThreadAccess)
        {
            Update();
        }
        else
        {
            _ = DispatcherQueue.TryEnqueue(Update);
        }
    }

    private async void PlayPauseButton_Click(object sender, RoutedEventArgs e)
    {
        VideoPlaybackSession? session = Volatile.Read(ref _videoSession);
        if (session is null)
        {
            return;
        }

        if (_isVideoPlaying)
        {
            CancelPlayback();
            await session.StopAsync();
            await UpdatePlaybackStateAsync(isPlaying: false);
            return;
        }

        TimeSpan startPosition = _videoPosition;
        if (session.Metadata.Duration > TimeSpan.Zero
            && startPosition >= session.Metadata.Duration - TimeSpan.FromMilliseconds(50))
        {
            startPosition = TimeSpan.Zero;
        }

        _ = StartPlaybackAsync(session, startPosition, Volatile.Read(ref _videoGeneration));
    }

    private async void PositionSlider_ValueChanged(object sender, RangeBaseValueChangedEventArgs e)
    {
        if (_isUpdatingPosition)
        {
            return;
        }

        VideoPlaybackSession? session = Volatile.Read(ref _videoSession);
        if (session is null)
        {
            return;
        }

        TimeSpan target = TimeSpan.FromSeconds(Math.Max(0, e.NewValue));
        _videoPosition = target;
        bool resumePlayback = _isVideoPlaying || _resumePlaybackAfterSeek;
        _resumePlaybackAfterSeek = resumePlayback;
        CancelPlayback();
        await UpdatePlaybackStateAsync(isPlaying: false);
        await UpdatePositionAsync(target, session.Metadata.Duration);

        CancellationTokenSource replacement = new();
        CancellationTokenSource? previous = Interlocked.Exchange(ref _seekDebounceCancellation, replacement);
        previous?.Cancel();
        previous?.Dispose();

        try
        {
            await Task.Delay(TimeSpan.FromMilliseconds(180), replacement.Token);
            if (!ReferenceEquals(session, Volatile.Read(ref _videoSession)))
            {
                return;
            }

            await session.StopAsync();
            int generation = Volatile.Read(ref _videoGeneration);
            if (_resumePlaybackAfterSeek)
            {
                _resumePlaybackAfterSeek = false;
                _ = StartPlaybackAsync(session, target, generation);
            }
            else
            {
                await session.DecodeAsync(
                    target,
                    frame => SubmitVideoFrame(session, generation, frame),
                    paceFrames: false,
                    maximumFrames: 1,
                    replacement.Token);
            }
        }
        catch (OperationCanceledException) when (replacement.IsCancellationRequested)
        {
        }
        catch (Exception exception)
        {
            await SetStateAsync(
                $"Video preview could not seek. {exception.Message}",
                isProgressActive: false,
                isVisible: true);
        }
        finally
        {
            if (ReferenceEquals(
                Interlocked.CompareExchange(ref _seekDebounceCancellation, null, replacement),
                replacement))
            {
                replacement.Dispose();
            }
        }
    }

    private async Task StartPlaybackAsync(
        VideoPlaybackSession session,
        TimeSpan startPosition,
        int generation)
    {
        if (!ReferenceEquals(session, Volatile.Read(ref _videoSession))
            || generation != Volatile.Read(ref _videoGeneration))
        {
            return;
        }

        CancellationTokenSource replacement = new();
        CancellationTokenSource? previous = Interlocked.Exchange(ref _playbackCancellation, replacement);
        previous?.Cancel();
        previous?.Dispose();
        _resumePlaybackAfterSeek = false;
        await UpdatePlaybackStateAsync(isPlaying: true);

        try
        {
            await session.DecodeAsync(
                startPosition,
                frame => SubmitVideoFrame(session, generation, frame),
                paceFrames: true,
                maximumFrames: null,
                replacement.Token);

            if (ReferenceEquals(session, Volatile.Read(ref _videoSession))
                && generation == Volatile.Read(ref _videoGeneration))
            {
                _videoPosition = session.Metadata.Duration;
                await UpdatePositionAsync(_videoPosition, session.Metadata.Duration);
            }
        }
        catch (OperationCanceledException) when (replacement.IsCancellationRequested)
        {
        }
        catch (Exception exception)
        {
            if (ReferenceEquals(session, Volatile.Read(ref _videoSession))
                && generation == Volatile.Read(ref _videoGeneration))
            {
                await SetStateAsync(
                    $"Video playback stopped. {exception.Message}",
                    isProgressActive: false,
                    isVisible: true);
            }
        }
        finally
        {
            if (ReferenceEquals(
                Interlocked.CompareExchange(ref _playbackCancellation, null, replacement),
                replacement))
            {
                replacement.Dispose();
                await UpdatePlaybackStateAsync(isPlaying: false);
            }
        }
    }

    private void SubmitVideoFrame(
        VideoPlaybackSession session,
        int generation,
        OwnedCpuFrame frame)
    {
        if (!ReferenceEquals(session, Volatile.Read(ref _videoSession))
            || generation != Volatile.Read(ref _videoGeneration))
        {
            frame.Dispose();
            return;
        }

        PreviewRendererSession? renderer = Volatile.Read(ref _renderer);
        TimeSpan timestamp = frame.Timestamp;
        if (renderer is null || !renderer.TrySubmitFrame(frame))
        {
            return;
        }

        _hasFrame = true;
        _videoPosition = timestamp;
        _ = UpdateVideoFrameStateAsync(timestamp, session.Metadata.Duration);
    }

    private Task ConfigureVideoTransportAsync(VideoMetadata metadata)
        => RunOnDispatcherAsync(() =>
        {
            VideoTransport.Visibility = Visibility.Visible;
            PositionSlider.Minimum = 0;
            PositionSlider.Maximum = Math.Max(metadata.Duration.TotalSeconds, 0.001);
            PositionSlider.Value = 0;
            PositionText.Text = $"{FormatTime(TimeSpan.Zero)} / {FormatTime(metadata.Duration)}";
            SetPlaybackButtonState(isPlaying: false);
        });

    private Task UpdateVideoFrameStateAsync(TimeSpan position, TimeSpan duration)
        => RunOnDispatcherAsync(() =>
        {
            SetState(string.Empty, isProgressActive: false, isVisible: false);
            UpdatePosition(position, duration);
        });

    private Task UpdatePositionAsync(TimeSpan position, TimeSpan duration)
        => RunOnDispatcherAsync(() => UpdatePosition(position, duration));

    private void UpdatePosition(TimeSpan position, TimeSpan duration)
    {
        _isUpdatingPosition = true;
        try
        {
            PositionSlider.Value = Math.Clamp(
                position.TotalSeconds,
                PositionSlider.Minimum,
                PositionSlider.Maximum);
            PositionText.Text = $"{FormatTime(position)} / {FormatTime(duration)}";
        }
        finally
        {
            _isUpdatingPosition = false;
        }
    }

    private Task UpdatePlaybackStateAsync(bool isPlaying)
    {
        _isVideoPlaying = isPlaying;
        return RunOnDispatcherAsync(() => SetPlaybackButtonState(isPlaying));
    }

    private void SetPlaybackButtonState(bool isPlaying)
    {
        PlayPauseIcon.Glyph = isPlaying ? "\uE769" : "\uE768";
        AutomationProperties.SetName(
            PlayPauseButton,
            isPlaying ? "Pause video preview" : "Play video preview");
    }

    private void CancelPlayback()
    {
        CancellationTokenSource? cancellation = Interlocked.Exchange(ref _playbackCancellation, null);
        cancellation?.Cancel();
        cancellation?.Dispose();
        Volatile.Read(ref _videoSession)?.Cancel();
        _isVideoPlaying = false;
    }

    private void BeginVideoCleanup()
    {
        _videoCleanupTask = DisposeVideoSessionAsync();
    }

    private async Task DisposeVideoSessionAsync()
    {
        await _videoLifecycleGate.WaitAsync();
        try
        {
            Interlocked.Increment(ref _videoGeneration);
            _resumePlaybackAfterSeek = false;
            CancelPlayback();

            CancellationTokenSource? seekCancellation =
                Interlocked.Exchange(ref _seekDebounceCancellation, null);
            seekCancellation?.Cancel();
            seekCancellation?.Dispose();

            VideoPlaybackSession? session = Interlocked.Exchange(ref _videoSession, null);
            if (session is not null)
            {
                App.Services.UntrackVideoPlaybackSession(session);
                await session.DisposeAsync();
            }

            _videoPosition = TimeSpan.Zero;
            await RunOnDispatcherAsync(() =>
            {
                VideoTransport.Visibility = Visibility.Collapsed;
                SetPlaybackButtonState(isPlaying: false);
            });
        }
        finally
        {
            _videoLifecycleGate.Release();
        }
    }

    private Task RunOnDispatcherAsync(Action action)
    {
        if (DispatcherQueue.HasThreadAccess)
        {
            action();
            return Task.CompletedTask;
        }

        var completion = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        if (!DispatcherQueue.TryEnqueue(() =>
            {
                try
                {
                    action();
                    completion.SetResult();
                }
                catch (Exception exception)
                {
                    completion.SetException(exception);
                }
            }))
        {
            completion.SetResult();
        }

        return completion.Task;
    }

    private static string FormatTime(TimeSpan value)
    {
        TimeSpan bounded = value < TimeSpan.Zero ? TimeSpan.Zero : value;
        return bounded.TotalHours >= 1
            ? bounded.ToString(@"h\:mm\:ss", CultureInfo.InvariantCulture)
            : bounded.ToString(@"m\:ss", CultureInfo.InvariantCulture);
    }

    private CancellationTokenSource ReplaceLoadCancellation(CancellationToken cancellationToken)
    {
        var replacement = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        CancellationTokenSource? previous = Interlocked.Exchange(ref _loadCancellation, replacement);
        previous?.Cancel();
        previous?.Dispose();
        return replacement;
    }

    private void CancelPendingLoad()
    {
        CancellationTokenSource? cancellation = Interlocked.Exchange(ref _loadCancellation, null);
        cancellation?.Cancel();
        cancellation?.Dispose();
    }

    private Task SetStateAsync(string message, bool isProgressActive, bool isVisible)
    {
        if (DispatcherQueue.HasThreadAccess)
        {
            SetState(message, isProgressActive, isVisible);
            return Task.CompletedTask;
        }

        var completion = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        if (!DispatcherQueue.TryEnqueue(() =>
            {
                SetState(message, isProgressActive, isVisible);
                completion.SetResult();
            }))
        {
            completion.SetResult();
        }

        return completion.Task;
    }

    private void SetState(string message, bool isProgressActive, bool isVisible)
    {
        StateText.Text = message;
        StateProgressRing.IsActive = isProgressActive;
        StateProgressRing.Visibility = isProgressActive ? Visibility.Visible : Visibility.Collapsed;
        StateOverlay.Visibility = isVisible ? Visibility.Visible : Visibility.Collapsed;
    }
}
