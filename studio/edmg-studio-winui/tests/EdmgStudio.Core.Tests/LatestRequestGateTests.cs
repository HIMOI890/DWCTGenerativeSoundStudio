using EdmgStudio.Core.Services;

namespace EdmgStudio.Core.Tests;

[TestClass]
public sealed class LatestRequestGateTests
{
    [TestMethod]
    public void SupersededResponseCannotPublishEvenWhenTransportCompletes()
    {
        var gate = new LatestRequestGate();
        using var first = gate.Begin();
        using var second = gate.Begin();
        Assert.IsTrue(first.Token.IsCancellationRequested);
        Assert.IsFalse(first.IsCurrent);
        Assert.IsTrue(second.IsCurrent);
        first.Dispose();
        Assert.IsTrue(second.IsCurrent);
    }

    [TestMethod]
    public void NavigatingAwayInvalidatesPendingReadAndAllowsFreshVisit()
    {
        var gate = new LatestRequestGate();
        using var first = gate.Begin();
        gate.Cancel();
        Assert.IsFalse(first.IsCurrent);
        Assert.IsTrue(first.Token.IsCancellationRequested);
        using var nextVisit = gate.Begin();
        Assert.IsTrue(nextVisit.IsCurrent);
    }

    [TestMethod]
    public void CallerCancellationPreventsPublication()
    {
        var gate = new LatestRequestGate();
        using var cancellation = new CancellationTokenSource();
        using var request = gate.Begin(cancellation.Token);
        cancellation.Cancel();
        Assert.IsFalse(request.IsCurrent);
        Assert.IsTrue(request.Token.IsCancellationRequested);
    }
}
