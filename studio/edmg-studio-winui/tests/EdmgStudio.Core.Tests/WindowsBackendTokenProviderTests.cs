using EdmgStudio.Core.Services;

namespace EdmgStudio.Core.Tests;

[TestClass]
public sealed class WindowsBackendTokenProviderTests
{
    [TestMethod]
    public void InvalidateClearsCachedAbsenceAcrossEntries()
    {
        var coordinator = new ProcessWideTokenCacheCoordinator();
        TokenCacheEntry first = coordinator.CreateEntry();
        TokenCacheEntry second = coordinator.CreateEntry();

        first.Store(null);
        second.Store(null);

        Assert.IsTrue(first.TryGet(out string? firstValue));
        Assert.IsNull(firstValue);
        Assert.IsTrue(second.TryGet(out string? secondValue));
        Assert.IsNull(secondValue);

        coordinator.Invalidate();

        Assert.IsFalse(first.TryGet(out _));
        Assert.IsFalse(second.TryGet(out _));
    }

    [TestMethod]
    public void InvalidateClearsCachedPresenceAcrossEntries()
    {
        var coordinator = new ProcessWideTokenCacheCoordinator();
        TokenCacheEntry first = coordinator.CreateEntry();
        TokenCacheEntry second = coordinator.CreateEntry();

        first.Store("persisted-token");
        second.Store("persisted-token");

        Assert.IsTrue(first.TryGet(out string? firstValue));
        Assert.AreEqual("persisted-token", firstValue);
        Assert.IsTrue(second.TryGet(out string? secondValue));
        Assert.AreEqual("persisted-token", secondValue);

        coordinator.Invalidate();

        Assert.IsFalse(first.TryGet(out _));
        Assert.IsFalse(second.TryGet(out _));
    }

    [TestMethod]
    public void StoreAfterInvalidationRefreshesTheCurrentVersion()
    {
        var coordinator = new ProcessWideTokenCacheCoordinator();
        TokenCacheEntry entry = coordinator.CreateEntry();

        entry.Store("stale-token");
        coordinator.Invalidate();
        Assert.IsFalse(entry.TryGet(out _));

        entry.Store("fresh-token");

        Assert.IsTrue(entry.TryGet(out string? refreshedValue));
        Assert.AreEqual("fresh-token", refreshedValue);
    }
}
