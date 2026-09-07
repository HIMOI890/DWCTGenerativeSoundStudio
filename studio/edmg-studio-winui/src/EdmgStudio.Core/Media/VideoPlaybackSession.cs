namespace EdmgStudio.Core.Media;

public sealed class VideoPlaybackSession : IAsyncDisposable
{
    private const string MaximumSpoolBytesEnvironmentVariable = "EDMG_STUDIO_VIDEO_SPOOL_MAX_BYTES";
    private const long DefaultMaximumSpoolBytes = 512L * 1024 * 1024;
    private readonly IVideoDecoder _decoder;
    private readonly SemaphoreSlim _operationGate = new(1, 1);
    private CancellationTokenSource? _decodeCancellation;
    private Task? _decodeTask;
    private int _disposed;

    private VideoPlaybackSession(string temporaryPath, VideoMetadata metadata, IVideoDecoder decoder)
    {
        TemporaryPath = temporaryPath;
        Metadata = metadata;
        _decoder = decoder;
    }

    public string TemporaryPath { get; }

    public VideoMetadata Metadata { get; }

    public static async Task<VideoPlaybackSession> CreateAsync(
        Stream source,
        MediaToolPaths tools,
        CancellationToken cancellationToken = default)
        => await CreateAsync(
            source,
            tools,
            knownContentLength: null,
            cancellationToken).ConfigureAwait(false);

    public static async Task<VideoPlaybackSession> CreateAsync(
        Stream source,
        MediaToolPaths tools,
        long? knownContentLength,
        CancellationToken cancellationToken = default)
        => await CreateAsync(
            source,
            new FfmpegVideoDecoder(tools),
            Path.GetTempPath(),
            knownContentLength,
            cancellationToken).ConfigureAwait(false);

    internal static async Task<VideoPlaybackSession> CreateAsync(
        Stream source,
        IVideoDecoder decoder,
        string temporaryDirectory,
        CancellationToken cancellationToken = default)
        => await CreateAsync(
            source,
            decoder,
            temporaryDirectory,
            knownContentLength: null,
            cancellationToken).ConfigureAwait(false);

    internal static async Task<VideoPlaybackSession> CreateAsync(
        Stream source,
        IVideoDecoder decoder,
        string temporaryDirectory,
        long? knownContentLength,
        CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(source);
        ArgumentNullException.ThrowIfNull(decoder);
        ArgumentException.ThrowIfNullOrWhiteSpace(temporaryDirectory);

        long maximumSpoolBytes = ResolveMaximumSpoolBytes();
        ValidateLength(GetRemainingLength(source, knownContentLength), maximumSpoolBytes);

        Directory.CreateDirectory(temporaryDirectory);
        string temporaryPath = Path.Combine(temporaryDirectory, $"edmg-winui-video-{Guid.NewGuid():N}.media");
        try
        {
            await using (var destination = new FileStream(
                temporaryPath,
                FileMode.CreateNew,
                FileAccess.Write,
                FileShare.Read,
                1024 * 1024,
                FileOptions.Asynchronous | FileOptions.SequentialScan))
            {
                await CopyToTemporaryFileAsync(source, destination, maximumSpoolBytes, cancellationToken)
                    .ConfigureAwait(false);
            }

            VideoMetadata metadata = await decoder.ProbeAsync(temporaryPath, cancellationToken).ConfigureAwait(false);
            return new VideoPlaybackSession(temporaryPath, metadata, decoder);
        }
        catch
        {
            TryDelete(temporaryPath);
            throw;
        }
    }

    public async Task DecodeAsync(
        TimeSpan startPosition,
        Action<EdmgStudio.Core.Graphics.OwnedCpuFrame> submitFrame,
        bool paceFrames = true,
        int? maximumFrames = null,
        CancellationToken cancellationToken = default)
    {
        ThrowIfDisposed();
        await StopAsync().ConfigureAwait(false);

        CancellationTokenSource decodeCancellation;
        Task decodeTask;
        await _operationGate.WaitAsync(cancellationToken).ConfigureAwait(false);
        try
        {
            ThrowIfDisposed();
            decodeCancellation = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
            decodeTask = _decoder.DecodeAsync(
                TemporaryPath,
                Metadata,
                startPosition,
                submitFrame,
                paceFrames,
                maximumFrames,
                decodeCancellation.Token);
            _decodeCancellation = decodeCancellation;
            _decodeTask = decodeTask;
        }
        finally
        {
            _operationGate.Release();
        }

        try
        {
            await decodeTask.ConfigureAwait(false);
        }
        finally
        {
            await _operationGate.WaitAsync(CancellationToken.None).ConfigureAwait(false);
            try
            {
                if (ReferenceEquals(_decodeCancellation, decodeCancellation))
                {
                    _decodeCancellation = null;
                    _decodeTask = null;
                }
            }
            finally
            {
                _operationGate.Release();
            }

            decodeCancellation.Dispose();
        }
    }

    public void Cancel()
    {
        try
        {
            _decodeCancellation?.Cancel();
        }
        catch (ObjectDisposedException)
        {
        }
    }

    public async Task StopAsync()
    {
        Task? decodeTask;
        await _operationGate.WaitAsync(CancellationToken.None).ConfigureAwait(false);
        try
        {
            _decodeCancellation?.Cancel();
            decodeTask = _decodeTask;
        }
        finally
        {
            _operationGate.Release();
        }

        if (decodeTask is not null)
        {
            try
            {
                await decodeTask.ConfigureAwait(false);
            }
            catch (OperationCanceledException)
            {
            }

            await _operationGate.WaitAsync(CancellationToken.None).ConfigureAwait(false);
            try
            {
                if (ReferenceEquals(_decodeTask, decodeTask))
                {
                    _decodeCancellation?.Dispose();
                    _decodeCancellation = null;
                    _decodeTask = null;
                }
            }
            finally
            {
                _operationGate.Release();
            }
        }
    }

    public async ValueTask DisposeAsync()
    {
        if (Interlocked.Exchange(ref _disposed, 1) != 0)
        {
            return;
        }

        await StopAsync().ConfigureAwait(false);
        _operationGate.Dispose();
        TryDelete(TemporaryPath);
    }

    private void ThrowIfDisposed()
    {
        ObjectDisposedException.ThrowIf(Volatile.Read(ref _disposed) != 0, this);
    }

    private static async Task CopyToTemporaryFileAsync(
        Stream source,
        Stream destination,
        long maximumSpoolBytes,
        CancellationToken cancellationToken)
    {
        byte[] buffer = new byte[1024 * 1024];
        long totalBytes = 0;
        while (true)
        {
            long remainingBudget = maximumSpoolBytes - totalBytes;
            if (remainingBudget < 0)
            {
                throw new InvalidDataException($"Video previews cannot exceed {FormatByteLimit(maximumSpoolBytes)} of temporary playback data.");
            }

            int requestLength = checked((int)Math.Min(buffer.Length, remainingBudget + 1));
            int read = await source.ReadAsync(buffer.AsMemory(0, requestLength), cancellationToken).ConfigureAwait(false);
            if (read == 0)
            {
                break;
            }

            totalBytes = checked(totalBytes + read);
            ValidateLength(totalBytes, maximumSpoolBytes);
            await destination.WriteAsync(buffer.AsMemory(0, read), cancellationToken).ConfigureAwait(false);
        }

        await destination.FlushAsync(cancellationToken).ConfigureAwait(false);
    }

    private static long? GetRemainingLength(Stream source, long? knownContentLength)
    {
        if (!source.CanSeek)
        {
            return knownContentLength;
        }

        long remaining = checked(source.Length - source.Position);
        return remaining < 0 ? throw new InvalidDataException("The video stream position is outside the available data.") : remaining;
    }

    private static void ValidateLength(long? length, long maximumSpoolBytes)
    {
        if (length is not null && length.Value > maximumSpoolBytes)
        {
            throw new InvalidDataException(
                $"Video previews cannot exceed {FormatByteLimit(maximumSpoolBytes)} of temporary playback data.");
        }
    }

    private static long ResolveMaximumSpoolBytes()
    {
        string? configured = Environment.GetEnvironmentVariable(MaximumSpoolBytesEnvironmentVariable)?.Trim();
        return long.TryParse(configured, out long value) && value > 0
            ? value
            : DefaultMaximumSpoolBytes;
    }

    private static string FormatByteLimit(long bytes)
    {
        const long kilobyte = 1024;
        const long megabyte = 1024 * kilobyte;
        const long gigabyte = 1024 * megabyte;
        return bytes switch
        {
            >= gigabyte => $"{bytes / (double)gigabyte:0.##} GB",
            >= megabyte => $"{bytes / (double)megabyte:0.##} MB",
            >= kilobyte => $"{bytes / (double)kilobyte:0.##} KB",
            _ => $"{bytes} bytes"
        };
    }

    private static void TryDelete(string path)
    {
        try
        {
            File.Delete(path);
        }
        catch (IOException)
        {
        }
        catch (UnauthorizedAccessException)
        {
        }
    }
}
