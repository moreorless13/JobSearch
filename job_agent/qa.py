from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pydantic as pydantic_module

from job_agent.config import get_model_name
from job_agent.events import WorkflowEvent
from job_agent.models import QAResult
from job_agent.state import QAEvaluationRecord, QAVerdict, StateStore, WEIGHT_DELTA_CAP, isoformat
from job_agent.tools.dedupe import normalize_text
from job_agent.tools.jobs import SOURCE_DISPLAY_NAMES, keyword_match_count, location_matches, salary_meets_floor
from job_agent.tools.sheets import rows_match

BaseModel = cast(Any, pydantic_module).BaseModel
Field = cast(Any, pydantic_module).Field

JOB_ACTIONABLE_EMAIL_CLASSIFICATIONS = {
    "Application Confirmation",
    "Interview Request",
    "Assessment Request",
    "Follow-Up Needed",
    "Rejection",
    "Offer",
}

HIGH_SIGNAL_SOURCES = {"company_sites", "greenhouse", "lever", "workday", "ashby"}
MID_SIGNAL_SOURCES = {"linkedin", "smartrecruiters", "google_jobs"}


class QAJudgeAdjustment(BaseModel):
    score_adjustment: float = Field(default=0.0, ge=-0.1, le=0.1)
    reasons: list[str] = Field(default_factory=list)


def clamp_score(value: float) -> float:
    return max(0.0, min(1.0, round(value, 3)))


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        pass
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(value, fmt)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC)
        except ValueError:
            continue
    return None


def latest_row_timestamp(row: dict[str, Any]) -> datetime | None:
    candidates = [
        row.get("last_email_update"),
        row.get("offer_date"),
        row.get("interview_date"),
        row.get("follow_up_date"),
        row.get("applied_date"),
        row.get("date_added"),
    ]
    parsed = [timestamp for timestamp in (parse_datetime(value) for value in candidates) if timestamp is not None]
    if not parsed:
        return None
    return max(parsed)


def source_quality_score(source: str | None) -> float:
    raw = str(source or "").strip().lower()
    normalized = normalize_text(source)
    normalized_high_signal = {normalize_text(value) for value in HIGH_SIGNAL_SOURCES}
    normalized_mid_signal = {normalize_text(value) for value in MID_SIGNAL_SOURCES}
    normalized_known_sources = {normalize_text(value) for value in SOURCE_DISPLAY_NAMES} | {
        normalize_text(value) for value in SOURCE_DISPLAY_NAMES.values()
    }
    if raw in HIGH_SIGNAL_SOURCES or normalized in normalized_high_signal:
        return 1.0
    if raw in MID_SIGNAL_SOURCES or normalized in normalized_mid_signal:
        return 0.75
    if raw in SOURCE_DISPLAY_NAMES or normalized in normalized_known_sources:
        return 0.55
    return 0.4


def freshness_score(job: dict[str, Any], stale_days: int) -> tuple[float, bool]:
    age = job.get("posting_age_days")
    if age is None:
        return 0.5, False
    try:
        age_days = max(int(age), 0)
    except (TypeError, ValueError):
        return 0.5, False
    if age_days > stale_days:
        return 0.1, True
    if age_days <= 7:
        return 1.0, False
    if age_days <= 14:
        return 0.7, False
    return 0.4, False


def summary_mentions_change(summary: str | None) -> bool:
    normalized = normalize_text(summary)
    return any(token in normalized for token in ("role ", "source ", "industry ", "+0 1", "-0 1"))


class QAEventDispatcher:
    def __init__(self, candidate_profile: dict[str, Any], state_store: StateStore) -> None:
        qa_settings = candidate_profile.get("qa") or {}
        env_llm_flag = os.getenv("JOB_AGENT_QA_LLM_JUDGE_ENABLED")
        llm_enabled = qa_settings.get("llm_judge_enabled", False)
        if env_llm_flag is not None:
            llm_enabled = env_llm_flag.lower() in {"1", "true", "yes"}

        self.candidate_profile = candidate_profile
        self.state_store = state_store
        self.approve_threshold = float(qa_settings.get("approve_threshold", 0.8))
        self.flag_threshold = float(qa_settings.get("flag_threshold", 0.6))
        self.llm_judge_enabled = bool(llm_enabled)
        self.duplicate_company_cooldown_days = int(qa_settings.get("duplicate_company_cooldown_days", 7))

    def _persist_result(
        self,
        *,
        workflow: str,
        event_type: WorkflowEvent,
        context: dict[str, Any],
        result: QAResult,
    ) -> None:
        self.state_store.append_qa_evaluation(
            QAEvaluationRecord(
                evaluation_id=str(uuid.uuid4()),
                timestamp=isoformat(datetime.now(UTC)),
                workflow=workflow,
                event_type=result.event_type,
                stage=result.stage,
                entity_key=result.entity_key,
                verdict=result.verdict,
                score=result.score,
                approve_threshold=result.approve_threshold,
                flag_threshold=result.flag_threshold,
                blocked_action=result.blocked_action,
                recommended_action=result.recommended_action,
                reasons=result.reasons,
                score_breakdown=result.score_breakdown,
                metadata={"event_type": event_type.value, "context_keys": sorted(context.keys())},
            )
        )

    def _build_result(
        self,
        *,
        event_type: WorkflowEvent,
        stage: str,
        entity_key: str | None,
        verdict: QAVerdict,
        score: float,
        blocked_action: str | None,
        recommended_action: str,
        reasons: list[str],
        score_breakdown: dict[str, float],
    ) -> QAResult:
        return QAResult(
            event_type=event_type.value,
            stage=stage,
            entity_key=entity_key,
            verdict=verdict,
            score=score,
            approve_threshold=self.approve_threshold,
            flag_threshold=self.flag_threshold,
            blocked_action=blocked_action,
            recommended_action=recommended_action,
            reasons=reasons,
            score_breakdown=score_breakdown,
        )

    def evaluate(
        self,
        *,
        workflow: str,
        event_type: WorkflowEvent,
        stage: str,
        entity_key: str | None,
        payload: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> QAResult:
        context = context or {}
        if event_type == WorkflowEvent.JOB_FOUND:
            result, hard_failure = self._evaluate_job_found(stage=stage, entity_key=entity_key, payload=payload, context=context)
        elif event_type == WorkflowEvent.EMAIL_RECEIVED:
            result, hard_failure = self._evaluate_email_received(stage=stage, entity_key=entity_key, payload=payload, context=context)
        elif event_type == WorkflowEvent.STRATEGY_REFLECTED:
            result, hard_failure = self._evaluate_strategy_reflected(stage=stage, entity_key=entity_key, payload=payload, context=context)
        else:
            result = QAResult(
                event_type=event_type.value,
                stage=stage,
                entity_key=entity_key,
                verdict="approve",
                score=1.0,
                approve_threshold=self.approve_threshold,
                flag_threshold=self.flag_threshold,
                recommended_action="proceed",
                reasons=["No QA rules are configured for this event yet."],
                score_breakdown={},
            )
            hard_failure = False

        if self.llm_judge_enabled and not hard_failure and self._should_use_llm_judge(event_type=event_type, result=result, payload=payload):
            result = self._apply_llm_adjustment(result=result, event_type=event_type, payload=payload, context=context)

        self._persist_result(workflow=workflow, event_type=event_type, context=context, result=result)
        return result

    def _should_use_llm_judge(self, *, event_type: WorkflowEvent, result: QAResult, payload: dict[str, Any]) -> bool:
        if event_type == WorkflowEvent.JOB_FOUND:
            return abs(result.score - self.approve_threshold) <= 0.08 or result.verdict == "flag"
        if event_type == WorkflowEvent.EMAIL_RECEIVED:
            classification = str((payload.get("classified") or {}).get("classification") or "")
            return classification == "Unclear" or result.verdict != "approve"
        return False

    def _apply_llm_adjustment(
        self,
        *,
        result: QAResult,
        event_type: WorkflowEvent,
        payload: dict[str, Any],
        context: dict[str, Any],
    ) -> QAResult:
        try:
            adjustment = self._call_llm_judge(event_type=event_type, payload=payload, context=context, result=result)
        except Exception:
            return result

        updated_score = clamp_score(result.score + adjustment.score_adjustment)
        updated_verdict = self._resolve_threshold_verdict(updated_score)
        return result.model_copy(
            update={
                "score": updated_score,
                "verdict": updated_verdict,
                "reasons": [*result.reasons, *adjustment.reasons],
            }
        )

    def _call_llm_judge(
        self,
        *,
        event_type: WorkflowEvent,
        payload: dict[str, Any],
        context: dict[str, Any],
        result: QAResult,
    ) -> QAJudgeAdjustment:
        import openai as openai_module

        client = cast(Any, openai_module).OpenAI()
        prompt = (
            "You are reviewing an automated job-search QA verdict.\n"
            f"Event type: {event_type.value}\n"
            f"Current score: {result.score}\n"
            f"Current verdict: {result.verdict}\n"
            f"Reasons: {result.reasons}\n"
            f"Payload: {payload}\n"
            f"Context: {context}\n"
            "Return a small score adjustment between -0.1 and 0.1 and brief reasons. "
            "Do not ignore hard constraints such as duplicates, salary floor, or missing identity data."
        )
        response = client.responses.parse(
            model=get_model_name(),
            input=prompt,
            text_format=QAJudgeAdjustment,
            max_output_tokens=300,
        )
        return response.output_parsed or QAJudgeAdjustment()

    def _resolve_threshold_verdict(self, score: float) -> QAVerdict:
        if score >= self.approve_threshold:
            return "approve"
        if score >= self.flag_threshold:
            return "flag"
        return "reject"

    def _recent_company_match(self, company: str | None, tracker_rows: list[dict[str, Any]]) -> bool:
        company_key = normalize_text(company)
        if not company_key:
            return False
        cutoff = datetime.now(UTC) - timedelta(days=self.duplicate_company_cooldown_days)
        for row in tracker_rows:
            if normalize_text(row.get("company")) != company_key:
                continue
            latest = latest_row_timestamp(row)
            if latest is None:
                continue
            if latest >= cutoff:
                return True
        return False

    def _evaluate_job_found(
        self,
        *,
        stage: str,
        entity_key: str | None,
        payload: dict[str, Any],
        context: dict[str, Any],
    ) -> tuple[QAResult, bool]:
        job = payload["job"]
        fit = payload["fit"]
        tracker_rows = context.get("tracker_rows") or []
        stale_days = int(self.candidate_profile["decision_thresholds"]["stale_days"])

        title = normalize_text(job.get("role_title"))
        target_roles = [normalize_text(role) for role in self.candidate_profile.get("target_roles", []) if role]
        title_match = any(role and role in title for role in target_roles)
        salary_value = job.get("salary_min")
        salary_match_value = 1.0 if salary_meets_floor(job, self.candidate_profile["salary_floor"]) else 0.0
        if salary_value is None:
            salary_match_value = 0.6
        keyword_hits, _ = keyword_match_count(job, self.candidate_profile.get("keywords", []))
        keyword_value = min(keyword_hits / 3, 1.0)
        if keyword_hits == 0 and int(fit.get("fit_score") or 0) >= 75:
            keyword_value = 0.5

        fresh_value, stale_posting = freshness_score(job, stale_days)
        duplicate_match = any(rows_match(existing=row, candidate=job, match_strategy="hybrid") for row in tracker_rows)
        recent_company_match = self._recent_company_match(job.get("company"), tracker_rows)
        missing_identity = not job.get("company") or not job.get("role_title")
        location_match_value = 1.0 if location_matches(job, self.candidate_profile) else 0.0

        score_breakdown = {
            "role_match": 0.30 if title_match else 0.0,
            "salary_match": round(0.20 * salary_match_value, 3),
            "location_match": round(0.15 * location_match_value, 3),
            "keyword_alignment": round(0.15 * keyword_value, 3),
            "source_quality": round(0.10 * source_quality_score(job.get("source")), 3),
            "freshness": round(0.10 * fresh_value, 3),
            "duplicate_penalty": -0.50 if duplicate_match or recent_company_match else 0.0,
            "stale_penalty": -0.20 if stale_posting else 0.0,
            "missing_critical_fields_penalty": -0.30 if missing_identity else 0.0,
        }
        score = clamp_score(sum(score_breakdown.values()))

        reasons: list[str] = []
        hard_verdict: QAVerdict | None = None
        if title_match:
            reasons.append("Role title aligns with target roles.")
        else:
            reasons.append("Role title does not clearly align with target roles.")
        if salary_value is None:
            reasons.append("Salary is missing, so QA applied a partial credit instead of a full pass.")
        elif not salary_meets_floor(job, self.candidate_profile["salary_floor"]):
            reasons.append("Listed salary is below the configured floor.")
            hard_verdict = "reject"
        if not location_match_value:
            reasons.append("Location does not satisfy the candidate profile.")
            hard_verdict = "reject"
        if duplicate_match:
            reasons.append("This job already exists in the tracker.")
            hard_verdict = "reject"
        elif recent_company_match:
            reasons.append("A recent tracker row already exists for this company inside the QA cooldown window.")
            hard_verdict = "flag"
        if stale_posting:
            reasons.append("Posting looks stale relative to the configured freshness window.")
        if missing_identity:
            reasons.append("Critical job fields are missing.")
            hard_verdict = hard_verdict or "flag"
        if keyword_value >= 0.66:
            reasons.append("Keyword alignment is strong.")
        elif keyword_value == 0:
            reasons.append("Keyword alignment is weak.")

        verdict = hard_verdict or self._resolve_threshold_verdict(score)
        blocked_action = None
        recommended_action = "proceed"
        if verdict == "flag":
            blocked_action = "tracker_sync"
            recommended_action = "manual_review"
        elif verdict == "reject":
            blocked_action = "job_intake"
            recommended_action = "skip_job"

        return (
            self._build_result(
                event_type=WorkflowEvent.JOB_FOUND,
                stage=stage,
                entity_key=entity_key,
                verdict=verdict,
                score=score,
                blocked_action=blocked_action,
                recommended_action=recommended_action,
                reasons=reasons,
                score_breakdown=score_breakdown,
            ),
            hard_verdict is not None,
        )

    def _evaluate_email_received(
        self,
        *,
        stage: str,
        entity_key: str | None,
        payload: dict[str, Any],
        context: dict[str, Any],
    ) -> tuple[QAResult, bool]:
        message = payload["message"]
        classified = payload["classified"]
        matched = payload["matched"]
        matched_row = payload.get("matched_row") or {}

        classification = str(classified.get("classification") or "Unclear")
        classification_confidence = float(classified.get("confidence") or 0.35)
        match_confidence = float(matched.get("confidence") or 0.2)
        actionable = classification in JOB_ACTIONABLE_EMAIL_CLASSIFICATIONS
        action_clarity = 1.0 if classified.get("action") else (0.8 if classification != "Unclear" else 0.2)
        parsed_date = parse_datetime(message.get("date"))
        if parsed_date is None:
            recency_value = 0.8
        else:
            age_days = max((datetime.now(UTC) - parsed_date).days, 0)
            recency_value = 1.0 if age_days <= 14 else 0.8 if age_days <= 30 else 0.6

        no_safe_match = actionable and not matched.get("matched") and not (classified.get("company") or classified.get("role_title"))
        score_breakdown = {
            "classification_confidence": round(0.40 * min(classification_confidence, 1.0), 3),
            "tracker_match_confidence": round(0.30 * min(match_confidence, 1.0), 3),
            "action_clarity": round(0.20 * action_clarity, 3),
            "recency": round(0.10 * recency_value, 3),
            "unclear_penalty": -0.30 if classification == "Unclear" else 0.0,
            "unsafe_match_penalty": -0.25 if no_safe_match else 0.0,
        }
        score = clamp_score(sum(score_breakdown.values()))

        reasons = [f"Email classified as {classification}."]
        hard_verdict: QAVerdict | None = None
        if classification == "Unclear":
            reasons.append("Classification confidence is too weak for autonomous tracker changes.")
            hard_verdict = "flag"
        if actionable and matched.get("matched"):
            reasons.append("Tracker match confidence is sufficient for the matched row.")
        elif actionable:
            reasons.append("No reliable tracker match was found for an actionable email.")
            hard_verdict = hard_verdict or "flag"
        if parsed_date is not None and recency_value < 0.5:
            reasons.append("The message looks stale for operational automation.")

        verdict = hard_verdict or self._resolve_threshold_verdict(score)
        blocked_action = None
        recommended_action = "proceed"
        if verdict in {"flag", "reject"}:
            blocked_action = "gmail_mutation"
            recommended_action = "manual_review"

        return (
            self._build_result(
                event_type=WorkflowEvent.EMAIL_RECEIVED,
                stage=stage,
                entity_key=entity_key,
                verdict=verdict,
                score=score,
                blocked_action=blocked_action,
                recommended_action=recommended_action,
                reasons=reasons,
                score_breakdown=score_breakdown,
            ),
            hard_verdict is not None,
        )

    def _evaluate_strategy_reflected(
        self,
        *,
        stage: str,
        entity_key: str | None,
        payload: dict[str, Any],
        context: dict[str, Any],
    ) -> tuple[QAResult, bool]:
        previous_snapshot = payload["previous_snapshot"]
        updated_snapshot = payload["updated_snapshot"]
        decisions = payload.get("decisions") or []
        outcomes = payload.get("outcomes") or []
        due_follow_ups = payload.get("due_follow_ups") or []

        def max_delta(collection_name: str) -> float:
            previous = getattr(previous_snapshot, collection_name)
            updated = getattr(updated_snapshot, collection_name)
            keys = set(previous) | set(updated)
            if not keys:
                return 0.0
            return max(abs(float(updated.get(key, 0.0)) - float(previous.get(key, 0.0))) for key in keys)

        any_changes = any(
            getattr(previous_snapshot, name) != getattr(updated_snapshot, name)
            for name in ("role_weights", "industry_weights", "source_weights", "subgoal_priorities")
        )
        evidence_value = 1.0 if outcomes or len(decisions) >= 3 else 0.4 if decisions else 0.0
        largest_delta = max(
            max_delta("role_weights"),
            max_delta("industry_weights"),
            max_delta("source_weights"),
        )
        bounded_value = 1.0 if largest_delta <= WEIGHT_DELTA_CAP else 0.0
        follow_up_priority = float(updated_snapshot.subgoal_priorities.get("follow_up_hygiene", 1.0))
        follow_up_value = 1.0 if 1.0 <= follow_up_priority <= 1.3 else 0.0
        explainable = (
            (not any_changes and str(updated_snapshot.reflection_summary).startswith("No strategy changes"))
            or (any_changes and summary_mentions_change(updated_snapshot.reflection_summary))
        )
        explainability_value = 1.0 if explainable else 0.2

        score_breakdown = {
            "evidence_sufficiency": round(0.35 * evidence_value, 3),
            "bounded_deltas": round(0.25 * bounded_value, 3),
            "follow_up_priority_bounds": round(0.20 * follow_up_value, 3),
            "explainability": round(0.20 * explainability_value, 3),
        }
        score = clamp_score(sum(score_breakdown.values()))

        reasons: list[str] = []
        hard_verdict: QAVerdict | None = None
        if evidence_value == 0.0:
            reasons.append("There is no recent evidence to justify a strategy rewrite.")
            hard_verdict = "flag"
        else:
            reasons.append(f"Reflection considered {len(decisions)} recent decisions, {len(outcomes)} outcomes, and {len(due_follow_ups)} follow-up tasks.")
        if bounded_value == 0.0:
            reasons.append("A strategy delta exceeded the configured safety cap.")
            hard_verdict = "reject"
        if follow_up_value == 0.0:
            reasons.append("Follow-up prioritization moved outside the allowed QA bounds.")
            hard_verdict = hard_verdict or "reject"
        if explainability_value < 1.0:
            reasons.append("The reflection summary does not clearly explain the proposed strategy change.")
            hard_verdict = hard_verdict or "flag"

        verdict = hard_verdict or self._resolve_threshold_verdict(score)
        blocked_action = None
        recommended_action = "proceed"
        if verdict in {"flag", "reject"}:
            blocked_action = "strategy_persist"
            recommended_action = "manual_review"

        return (
            self._build_result(
                event_type=WorkflowEvent.STRATEGY_REFLECTED,
                stage=stage,
                entity_key=entity_key,
                verdict=verdict,
                score=score,
                blocked_action=blocked_action,
                recommended_action=recommended_action,
                reasons=reasons,
                score_breakdown=score_breakdown,
            ),
            hard_verdict is not None,
        )
