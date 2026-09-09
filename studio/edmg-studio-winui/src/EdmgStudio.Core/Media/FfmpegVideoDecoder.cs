using System.Buffers;
using System.Diagnostics;
using System.Globalization;
using EdmgStudio.Core.Graphics;

namespace EdmgStudio.Core.Media;

internal sealed class FfmpegVideoDecoder(MediaToolPaths tools) : IVideoDecoder
{
    public async Task<VideoMetadata> ProbeAsync(string sourcePath, CancellationToken cancellationToken = default)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(sourcePath);

        using var process = new Process
        {
            StartInfo = CreateProbeStartInfo(sourcePath),
            EnableRaisingEvents = true
        };

        try
        {
            StartProcess(process, "FFprobe");
            Task<string> stdoutTask = process.StandardOutput.ReadToEndAsync(cancellationToken);
            Task<string> stderrTask = process.StandardError.ReadToEndAsync(cancellationToken);
            await process.WaitForExitAsync(cancellationToken).ConfigureAwait(false);
            string stdout = await stdoutTask.ConfigureAwait(false);
            string stderr = await stderrTask.ConfigureAwait(false);

            if (process.ExitCode != 0)
            {
                throw new InvalidDataException(
                    $"FFprobe could not inspect the selected video (exit {process.ExitCode}): {NormalizeError(stderr)}");
            }

            return VideoMetadata.ParseFfprobeJson(stdout);
        }
        catch (OperationCanceledException)
        {
            KillProcess(process);
            throw;
        }
    }

    public async Task DecodeAsync(
        string sourcePath,
        VideoMetadata metadata,
        TimeSpan startPosition,
        Action<OwnedCpuFrame> submitFrame,
        bool paceFrames,
        int? maximumFrames,
        CancellationToken cancellationToken)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(sourcePath);
        ArgumentNullException.ThrowIfNull(metadata);
        ArgumentNullException.ThrowIfNull(submitFrame);

        TimeSpan boundedStart = startPosition < TimeSpan.Zero
            ? TimeSpan.Zero
            : metadata.Duration > TimeSpan.Zero && startPosition > metadata.Duration
                ? metadata.Duration
                : startPosition;
        int stride = checked(metadata.Width * FrameLayout.BytesPerPixel);
        int frameLength = FrameLayout.Validate(metadata.Width, metadata.Height, stride, checked(stride * metadata.Height))
            .TightBufferLength;

        using var process = new Process
        {
            StartInfo = CreateDecodeStartInfo(sourcePath, boundedStart),
            EnableRaisingEvents = true
        };

        using CancellationTokenRegistration cancellationRegistration = cancellationToken.Register(
            static state => KillProcess((Process)state!),
            process);
        bool processStarted = false;

        try
        {
            StartProcess(process, "FFmpeg");
            processStarted = true;
            Task<string> stderrTask = process.StandardError.ReadToEndAsync();
            Stopwatch playbackClock = Stopwatch.StartNew();
            int frameIndex = 0;

            while (!maximumFrames.HasValue || frameIndex < maximumFrames.Value)
            {
                cancellationToken.ThrowIfCancellationRequested();
                IMemoryOwner<byte> owner = MemoryPool<byte>.Shared.Rent(frameLength);
                bool hasFrame;
                try
                {
                    hasFrame = await RawFrameReader.ReadFrameAsync(
                        process.StandardOutput.BaseStream,
                        owner.Memory[..frameLength],
                        cancellationToken).ConfigureAwait(false);
                }
                catch
                {
                    owner.Dispose();
                    throw;
                }

                if (!hasFrame)
                {
                    owner.Dispose();
                    break;
                }

                TimeSpan relativeTimestamp = TimeSpan.FromSeconds(frameIndex / metadata.FramesPerSecond);
                OwnedCpuFrame frame = OwnedCpuFrame.Create(
                    owner,
                    frameLength,
                    metadata.Width,
                    metadata.Height,
                    stride,
                    FramePixelFormat.Bgra8,
                    timestamp: boundedStart + relativeTimestamp);
                try
                {
                    // The frame must own its pooled buffer before an awaited delay:
                    // canceling playback during pacing must release that buffer.
                    if (paceFrames)
                    {
                        TimeSpan delay = relativeTimestamp - playbackClock.Elapsed;
                        if (delay > TimeSpan.Zero)
                        {
                            await Task.Delay(delay, cancellationToken).ConfigureAwait(false);
                        }
                    }

                    submitFrame(frame);
                }
                catch
                {
                    frame.Dispose();
                    throw;
                }

                frameIndex++;
            }

            if (maximumFrames.HasValue && frameIndex >= maximumFrames.Value)
            {
                KillProcess(process);
            }

            await process.WaitForExitAsync(CancellationToken.None).ConfigureAwait(false);
            string stderr = await stderrTask.ConfigureAwait(false);
            if (!cancellationToken.IsCancellationRequested
                && !maximumFrames.HasValue
                && process.ExitCode != 0)
            {
                throw new InvalidDataException(
                    $"FFmpeg could not decode the selected video (exit {process.ExitCode}): {NormalizeError(stderr)}");
            }
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            KillProcess(process);
            throw;
        }
        finally
        {
            if (processStarted)
            {
                KillProcess(process);
                if (!process.HasExited)
                {
                    await process.WaitForExitAsync(CancellationToken.None).ConfigureAwait(false);
                }
            }
        }
    }

    private ProcessStartInfo CreateProbeStartInfo(string sourcePath)
    {
        var startInfo = CreateRedirectedStartInfo(tools.FfprobePath);
        startInfo.ArgumentList.Add("-v");
        startInfo.ArgumentList.Add("error");
        startInfo.ArgumentList.Add("-print_format");
        startInfo.ArgumentList.Add("json");
        startInfo.ArgumentList.Add("-show_streams");
        startInfo.ArgumentList.Add("-show_format");
        startInfo.ArgumentList.Add(sourcePath);
        return startInfo;
    }

    private ProcessStartInfo CreateDecodeStartInfo(string sourcePath, TimeSpan startPosition)
    {
        var startInfo = CreateRedirectedStartInfo(tools.FfmpegPath);
        startInfo.ArgumentList.Add("-hide_banner");
        startInfo.ArgumentList.Add("-loglevel");
        startInfo.ArgumentList.Add("error");
        startInfo.ArgumentList.Add("-ss");
        startInfo.ArgumentList.Add(startPosition.TotalSeconds.ToString("0.######", CultureInfo.InvariantCulture));
        startInfo.ArgumentList.Add("-i");
        startInfo.ArgumentList.Add(sourcePath);
        startInfo.ArgumentList.Add("-map");
        startInfo.ArgumentList.Add("0:v:0");
        startInfo.ArgumentList.Add("-an");
        startInfo.ArgumentList.Add("-sn");
        startInfo.ArgumentList.Add("-dn");
        startInfo.ArgumentList.Add("-f");
        startInfo.ArgumentList.Add("rawvideo");
        startInfo.ArgumentList.Add("-pix_fmt");
        startInfo.ArgumentList.Add("bgra");
        startInfo.ArgumentList.Add("pipe:1");
        return startInfo;
    }

    private static ProcessStartInfo CreateRedirectedStartInfo(string fileName)
        => new()
        {
            FileName = fileName,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true
        };

    private static void StartProcess(Process process, string toolName)
    {
        try
        {
            if (!process.Start())
            {
                throw new InvalidOperationException($"{toolName} did not start.");
            }
        }
        catch (Exception exception) when (exception is System.ComponentModel.Win32Exception or InvalidOperationException)
        {
            throw new InvalidOperationException(
                $"{toolName} is unavailable. Install or stage FFmpeg and FFprobe, or configure the EDMG media-tool environment variables.",
                exception);
        }
    }

    private static void KillProcess(Process process)
    {
        try
        {
            if (!process.HasExited)
            {
                process.Kill(entireProcessTree: true);
            }
        }
        catch (InvalidOperationException)
        {
        }
        catch (System.ComponentModel.Win32Exception)
        {
        }
    }

    private static string NormalizeError(string error)
    {
        string normalized = error.Trim();
        return string.IsNullOrWhiteSpace(normalized) ? "No diagnostic output was produced." : normalized;
    }
}
