namespace EdmgStudio.Core.Services;

internal sealed class ProcessWideTokenCacheCoordinator
{
    private long _version;

    public TokenCacheEntry CreateEntry() => new(this);

    public void Invalidate() => Interlocked.Increment(ref _version);

    internal long CurrentVersion => Volatile.Read(ref _version);
}

internal sealed class TokenCacheEntry(ProcessWideTokenCacheCoordinator coordinator)
{
    private readonly ProcessWideTokenCacheCoordinator _coordinator = coordinator;
    private string? _value;
    private long _observedVersion = -1;
    private int _hasValue;

    public bool TryGet(out string? value)
    {
        long currentVersion = _coordinator.CurrentVersion;
        if (Volatile.Read(ref _hasValue) != 0 && Volatile.Read(ref _observedVersion) == currentVersion)
        {
            value = _value;
            return true;
        }

        value = null;
        return false;
    }

    public void Store(string? value)
    {
        _value = value;
        Volatile.Write(ref _observedVersion, _coordinator.CurrentVersion);
        Volatile.Write(ref _hasValue, 1);
    }
}
