namespace EdmgStudio.Core.Services;

/// <summary>Cancels superseded reads and guards publication even if a transport ignores cancellation.</summary>
public sealed class LatestRequestGate
{
    private readonly object _sync = new();
    private Request? _current;

    public Request Begin(CancellationToken cancellationToken = default)
    {
        var request = new Request(this, cancellationToken);
        Request? previous;
        lock (_sync)
        {
            previous = _current;
            _current = request;
        }
        previous?.Cancel();
        return request;
    }

    public void Cancel()
    {
        Request? previous;
        lock (_sync)
        {
            previous = _current;
            _current = null;
        }
        previous?.Cancel();
    }

    public sealed class Request : IDisposable
    {
        private readonly LatestRequestGate _owner;
        private readonly object _sync = new();
        private readonly CancellationTokenSource _cancellation;
        private bool _disposed;

        internal Request(LatestRequestGate owner, CancellationToken cancellationToken)
        {
            _owner = owner;
            _cancellation = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
            Token = _cancellation.Token;
        }

        public CancellationToken Token { get; }
        public bool IsCurrent
        {
            get
            {
                lock (_owner._sync)
                    return ReferenceEquals(_owner._current, this) && !Token.IsCancellationRequested;
            }
        }

        internal void Cancel()
        {
            lock (_sync)
                if (!_disposed) _cancellation.Cancel();
        }

        public void Dispose()
        {
            lock (_owner._sync)
                if (ReferenceEquals(_owner._current, this)) _owner._current = null;
            lock (_sync)
            {
                if (_disposed) return;
                _disposed = true;
                _cancellation.Dispose();
            }
        }
    }
}
