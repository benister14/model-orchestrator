"""Cross-provider judge.  [P2]

Scores worker output on a rubric using a judge from a *different* provider than
the worker (no in-group bias — safety invariant #3). If the score falls below the
escalation floor, a meta-judge (resolved from config.roles.judge.meta) re-reviews
and its verdict becomes authoritative.

judge(output, task, cfg) -> {
    "judge_model":  str,           # primary judge
    "score":        float,         # primary judge score 0.0-1.0
    "reasoning":    str,
    "escalated":    bool,
    "meta":         dict | None,   # {"model", "score", "reasoning"} when escalated
    "final_score":  float,         # meta score if escalated, else primary score
    "passed":       bool,          # final_score >= floor
}
"""
from __future__ import annotations

import json
import re

from .adapters import get_adapter
from .config import api_model_of, effective_models


class JudgeProviderError(Exception):
    """Raised when the judge would share a provider with the worker/author."""


class JudgeResponseError(Exception):
    """Raised when the judge model does not return a parseable score."""


_RUBRIC_PROMPT = """You are an impartial evaluator. Assess the RESPONSE to the TASK on:
- correctness: factually and logically correct?
- completeness: does it fully address the task?
- logical_consistency: internally consistent?
- risk: unsafe / harmful / policy-violating content lowers the score.

TASK:
{task}

RESPONSE:
{output}

Return ONLY a JSON object (no prose, no markdown fence) of this exact shape:
{{"score": <float between 0.0 and 1.0>, "reasoning": "<one or two sentences>"}}
where score is your overall confidence the RESPONSE is acceptable."""


def _provider_of(model_name: str, cfg: dict) -> str:
    prov = cfg.get("models", {}).get(model_name, {}).get("provider")
    if not prov:
        raise JudgeResponseError(f"model '{model_name}' not found in config")
    return prov


def _assert_distinct(judge_model: str, worker_provider: str, cfg: dict) -> None:
    judge_provider = _provider_of(judge_model, cfg)
    if judge_provider == worker_provider:
        raise JudgeProviderError(
            f"judge '{judge_model}' (provider '{judge_provider}') shares the "
            f"worker's provider '{worker_provider}' — judge must be a third party"
        )


def _parse(text: str) -> tuple[float, str]:
    match = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not match:
        raise JudgeResponseError(f"no JSON in judge response: {(text or '')[:200]!r}")
    try:
        data = json.loads(match.group(0))
        score = float(data["score"])
        reasoning = str(data.get("reasoning", ""))
    except (ValueError, KeyError, TypeError) as e:
        raise JudgeResponseError(f"bad judge response {text[:200]!r}: {e}") from e
    return max(0.0, min(1.0, score)), reasoning


def _run(judge_model: str, output: str, task: dict, cfg: dict) -> tuple[float, str]:
    prompt = _RUBRIC_PROMPT.format(task=task.get("description", ""), output=output)
    provider = _provider_of(judge_model, cfg)
    # 2048, not ~1024: verbose judges (gemini reasoning) overran the smaller cap and
    # truncated their {"score":...} JSON mid-string -> JudgeResponseError. Headroom fixes it.
    raw = get_adapter(provider).complete(
        prompt=prompt, model=api_model_of(judge_model, cfg), max_tokens=2048)
    return _parse(raw)


def _pick_meta(worker_provider: str, primary_model: str, task: dict, cfg: dict) -> str | None:
    """Choose a meta-judge whose provider differs from the worker's (invariant #3).

    Prefers the configured roles.judge.meta when its provider is valid; otherwise
    searches the active roster for a judge-capable model on a third provider
    (preferring one that also differs from the primary judge). Sensitive tasks
    constrain the meta to the trusted lane. Returns None when no valid meta
    exists — the caller then keeps the primary judge's score rather than raising,
    so `--judge` never aborts just because routing picked a worker that shares the
    configured meta's provider (the live failure: anthropic worker + opus meta).
    """
    sensitive = bool(task.get("sensitive"))
    trusted = set(cfg["lanes"]["trusted"]["providers"])
    primary_provider = _provider_of(primary_model, cfg)
    models = effective_models(cfg)

    def ok(m: dict) -> bool:
        prov = m.get("provider")
        if prov == worker_provider:
            return False
        if sensitive and prov not in trusted:
            return False
        return True

    configured = cfg.get("roles", {}).get("judge", {}).get("meta")
    if configured and configured in models and ok(models[configured]):
        return configured

    JUDGE_ROLES = {"meta_judge", "judge", "judge_sensitive"}
    pool = [n for n, m in models.items() if ok(m) and (JUDGE_ROLES & set(m.get("roles", [])))]
    if not pool:  # last resort: any active third-provider model
        pool = [n for n, m in models.items() if ok(m)]
    # prefer a provider distinct from the primary judge too (true third opinion)
    pool.sort(key=lambda n: models[n]["provider"] == primary_provider)
    return pool[0] if pool else None


def judge(output: str, task: dict, cfg: dict) -> dict:
    worker_model = task.get("model")
    if not worker_model:
        raise JudgeResponseError("task['model'] (the worker model) is required to judge")
    worker_provider = _provider_of(worker_model, cfg)

    judge_role = cfg.get("roles", {}).get("judge", {})
    primary_model = judge_role.get("default")
    if not primary_model:
        raise JudgeResponseError("no roles.judge.default configured")
    _assert_distinct(primary_model, worker_provider, cfg)

    score, reasoning = _run(primary_model, output, task, cfg)

    floor = cfg.get("thresholds", {}).get("escalate_to_claude_when_judge_below", 0.75)
    escalated = False
    meta = None
    final_score = score

    if score < floor:
        meta_model = _pick_meta(worker_provider, primary_model, task, cfg)
        if meta_model:
            meta_score, meta_reasoning = _run(meta_model, output, task, cfg)
            escalated = True
            meta = {"model": meta_model, "score": meta_score, "reasoning": meta_reasoning}
            final_score = meta_score
        # else: no valid cross-provider meta available -> keep the primary judge's
        # score (do not raise; the primary verdict still stands).

    return {
        "judge_model": primary_model,
        "score": score,
        "reasoning": reasoning,
        "escalated": escalated,
        "meta": meta,
        "final_score": final_score,
        "passed": final_score >= floor,
    }
