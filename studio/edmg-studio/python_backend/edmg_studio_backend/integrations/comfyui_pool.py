from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Iterable

from . import comfyui as comfy


@dataclass
class NodeStatus:
    url: str
    healthy: bool = True
    last_check_ts: float = 0.0
    last_error: str | None = None

    inflight: int = 0
    max_inflight: int = 1

    # cost/speed hints (lower cost is preferred)
    cost: float = 0.0
    tags: set[str] = field(default_factory=set)

    # capability cache (node class names present in /object_info)
    node_classes: set[str] = field(default_factory=set)
    last_caps_ts: float = 0.0
    caps_error: str | None = None

    # supported checkpoints (optional config hints)
    checkpoints: set[str] = field(default_factory=set)
    checkpoint_regex: list[re.Pattern] = field(default_factory=list)


class ComfyUINodePool:
    """Multi-node scheduler for ComfyUI endpoints with capability + cost-aware routing.

    Features:
      - least-inflight routing with per-node concurrency caps
      - throttled health checks (via /object_info)
      - capability checks (required node class names + tags)
      - optional checkpoint compatibility filtering
      - cost-aware selection (lower cost preferred)
    """

    def __init__(
        self,
        nodes: list[dict[str, Any]],
        default_max_inflight: int = 1,
        health_check_interval_s: float = 10.0,
        capability_refresh_interval_s: float = 30.0,
    ):
        self._lock = threading.Lock()
        self._health_interval = float(health_check_interval_s)
        self._caps_interval = float(capability_refresh_interval_s)

        self._nodes: dict[str, NodeStatus] = {}
        for nd in nodes:
            url = str(nd.get("url", "")).strip().rstrip("/")
            if not url:
                continue
            max_inflight = int(nd.get("max_inflight") or default_max_inflight or 1)
            cost = float(nd.get("cost") or 0.0)
            tags = set([str(t).strip() for t in (nd.get("tags") or []) if str(t).strip()])

            checkpoints = set([str(c).strip() for c in (nd.get("checkpoints") or []) if str(c).strip()])
            cregex_raw = nd.get("checkpoint_regex") or []
            cregex: list[re.Pattern] = []
            for pat in cregex_raw:
                try:
                    cregex.append(re.compile(str(pat)))
                except Exception:
                    pass

            st = NodeStatus(
                url=url,
                max_inflight=max(1, max_inflight),
                cost=cost,
                tags=tags,
                checkpoints=checkpoints,
                checkpoint_regex=cregex,
            )
            self._nodes[url] = st

        if not self._nodes:
            raise ValueError("ComfyUINodePool requires at least one node URL.")

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            out: list[dict[str, Any]] = []
            for n in self._nodes.values():
                out.append(
                    {
                        "url": n.url,
                        "healthy": n.healthy,
                        "inflight": n.inflight,
                        "max_inflight": n.max_inflight,
                        "cost": n.cost,
                        "tags": sorted(n.tags),
                        "last_check_ts": n.last_check_ts,
                        "last_error": n.last_error,
                        "caps_last_ts": n.last_caps_ts,
                        "caps_error": n.caps_error,
                        "capabilities": {
                            "node_classes_sample": sorted(list(n.node_classes))[:25],
                            "node_classes_count": len(n.node_classes),
                        },
                    }
                )
            return out

    def diagnose(self, requirements: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return a user-friendly compatibility report for the given requirements."""
        requirements = requirements or {}
        report: dict[str, Any] = {"compatible": [], "busy_compatible": [], "incompatible": [], "unhealthy": []}

        with self._lock:
            for n in self._nodes.values():
                self._check_health_if_needed(n)
                if not n.healthy:
                    report["unhealthy"].append({"url": n.url, "error": n.last_error})
                    continue

                ok, reasons = self._matches(n, requirements)
                busy = n.inflight >= n.max_inflight
                if ok and not busy:
                    report["compatible"].append(n.url)
                elif ok and busy:
                    report["busy_compatible"].append(n.url)
                else:
                    report["incompatible"].append({"url": n.url, "reasons": reasons, "tags": sorted(n.tags)})

        return report

    # -------------------------
    # Health / capability checks
    # -------------------------

    def _check_health_if_needed(self, node: NodeStatus) -> None:
        now = time.time()
        if node.last_check_ts and (now - node.last_check_ts) < self._health_interval:
            return
        node.last_check_ts = now
        try:
            comfy.get_object_info(node.url)  # cheap if up
            node.healthy = True
            node.last_error = None
        except Exception as e:
            node.healthy = False
            node.last_error = str(e)

    def _refresh_caps_if_needed(self, node: NodeStatus) -> None:
        now = time.time()
        if node.last_caps_ts and (now - node.last_caps_ts) < self._caps_interval:
            return
        node.last_caps_ts = now
        try:
            info = comfy.get_object_info(node.url)
            node.node_classes = set((info or {}).keys())
            node.caps_error = None
        except Exception as e:
            node.caps_error = str(e)
            node.node_classes = set()

    # -------------------------
    # Matching / scoring
    # -------------------------

    @staticmethod
    def _norm_list(x: Any) -> list[str]:
        if not x:
            return []
        if isinstance(x, str):
            return [x]
        if isinstance(x, (list, tuple, set)):
            return [str(i) for i in x]
        return [str(x)]

    def _supports_checkpoint(self, node: NodeStatus, checkpoint_name: str | None) -> bool:
        if not checkpoint_name:
            return True
        ck = str(checkpoint_name).strip()
        if not ck:
            return True
        if node.checkpoints and ck in node.checkpoints:
            return True
        if node.checkpoints:
            # explicit list exists but this ckpt isn't in it
            return False
        if node.checkpoint_regex:
            return any(p.search(ck) for p in node.checkpoint_regex)
        return True  # no hints => assume OK

    def _matches(self, node: NodeStatus, requirements: dict[str, Any]) -> tuple[bool, list[str]]:
        reasons: list[str] = []

        # tags
        req_tags = set([t.strip() for t in self._norm_list(requirements.get("tags")) if t.strip()])
        if req_tags and not req_tags.issubset(node.tags):
            reasons.append(f"missing_tags={sorted(list(req_tags - node.tags))}")

        # required ComfyUI node classes
        req_nodes = set([n.strip() for n in self._norm_list(requirements.get("node_classes")) if n.strip()])
        if req_nodes:
            # ensure we have a fresh-enough cache if possible
            self._refresh_caps_if_needed(node)
            missing = req_nodes - node.node_classes
            if missing:
                reasons.append(f"missing_node_classes={sorted(list(missing))}")

        # checkpoint compatibility
        ckpt = requirements.get("checkpoint")
        if not self._supports_checkpoint(node, ckpt):
            reasons.append("checkpoint_unsupported")

        return (len(reasons) == 0), reasons

    def _score(self, node: NodeStatus, requirements: dict[str, Any]) -> float:
        """Lower score wins."""
        # base load factor
        load = node.inflight / max(1, node.max_inflight)

        # estimated cost factor
        est_steps = float(requirements.get("est_steps") or 0.0)
        est_frames = float(requirements.get("est_frames") or 1.0)
        est = max(1.0, est_steps) * max(1.0, est_frames)

        # scale so typical est doesn't dominate unless cost differs
        cost_term = node.cost * (est / 100.0)

        # optional penalty for unhealthy-but-not-yet-detected (caps error)
        caps_penalty = 0.25 if node.caps_error else 0.0

        return load + cost_term + caps_penalty

    # -------------------------
    # Public API
    # -------------------------

    def acquire(self, requirements: dict[str, Any] | None = None, block: bool = True, timeout_s: float = 30.0) -> str:
        """Acquire a node slot and return its URL.

        requirements may include:
          - tags: list[str]
          - node_classes: list[str] (ComfyUI class names required to exist)
          - checkpoint: str (checkpoint filename, optional hint)
          - est_steps: int/float (for cost scoring)
          - est_frames: int/float (for cost scoring)
        """
        requirements = requirements or {}
        deadline = time.time() + float(timeout_s)

        while True:
            with self._lock:
                candidates: list[tuple[float, NodeStatus]] = []

                for n in self._nodes.values():
                    self._check_health_if_needed(n)
                    if not n.healthy:
                        continue
                    if n.inflight >= n.max_inflight:
                        continue

                    ok, _reasons = self._matches(n, requirements)
                    if not ok:
                        continue

                    candidates.append((self._score(n, requirements), n))

                if candidates:
                    candidates.sort(key=lambda x: (x[0], x[1].inflight, x[1].url))
                    chosen = candidates[0][1]
                    chosen.inflight += 1
                    return chosen.url

            if not block:
                raise RuntimeError("No available ComfyUI nodes matching requirements (busy/unhealthy/incompatible).")
            if time.time() > deadline:
                raise RuntimeError("Timed out waiting for a free compatible ComfyUI node slot.")
            time.sleep(0.1)

    def release(self, url: str) -> None:
        with self._lock:
            n = self._nodes.get(url.rstrip("/"))
            if not n:
                return
            n.inflight = max(0, n.inflight - 1)
