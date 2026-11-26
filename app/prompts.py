import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

from app.telemetry import logger

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


@dataclass
class PromptTemplate:
    name: str
    version: str
    model_role: str
    temperature: float
    max_tokens: int
    schema: str
    system_template: str
    user_template: str


def escape_untrusted_text(text: str) -> str:
    """Escapes delimiter tokens so untrusted user data cannot break out of <<<...>>> blocks."""
    if not text:
        return ""
    escaped = text.replace("<<<", "< < <").replace(">>>", "> > >")
    return escaped


def _parse_frontmatter(raw: str) -> Tuple[Dict[str, str], str]:
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", raw, flags=re.DOTALL)
    if not match:
        return {}, raw
    meta_block, body = match.group(1), match.group(2)
    meta: Dict[str, str] = {}
    for line in meta_block.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        k, v = line.split(":", 1)
        meta[k.strip()] = v.strip()
    return meta, body


def load_prompt_template(scenario: str = "study_kit", version: str = "v1") -> PromptTemplate:
    """Loads a versioned prompt template from prompts/<scenario>/<version>.md."""
    path = PROMPTS_DIR / scenario / f"{version}.md"
    if not path.exists():
        raise FileNotFoundError(f"Prompt template not found: {path}")

    raw = path.read_text(encoding="utf-8")
    meta, body = _parse_frontmatter(raw)

    sys_match = re.search(r"^# SYSTEM\s*\n(.*?)(?=^# USER\s*$|\Z)", body, flags=re.DOTALL | re.MULTILINE)
    usr_match = re.search(r"^# USER\s*\n(.*)$", body, flags=re.DOTALL | re.MULTILINE)

    system_tpl = sys_match.group(1).strip() if sys_match else ""
    user_tpl = usr_match.group(1).strip() if usr_match else ""

    return PromptTemplate(
        name=meta.get("name", scenario),
        version=meta.get("version", version),
        model_role=meta.get("model_role", "assistant"),
        temperature=float(meta.get("temperature", "0.2")),
        max_tokens=int(meta.get("max_tokens", "8192")),
        schema=meta.get("schema", "StudyKitResponse"),
        system_template=system_tpl,
        user_template=user_tpl,
    )


def render_study_prompt(
    material: str,
    topic: str,
    card_count: int,
    quiz_count: int,
    version: str = "v1",
    validation_feedback: Optional[str] = None,
) -> Tuple[str, str, Dict[str, Any]]:
    """
    Renders system and user prompt from versioned template, escaping untrusted user inputs.
    Logs context composition telemetry without logging raw text or PII.
    """
    tpl = load_prompt_template("study_kit", version)

    if card_count > 0:
        card_req = f"РІВНО {card_count} флеш-карток (id 1..{card_count})"
    else:
        card_req = "оптимальну кількість карток (від 5 до 20 залежно від насиченості тексту)"

    if quiz_count > 0:
        quiz_req = f"РІВНО {quiz_count} тестових питань (id 1..{quiz_count})"
    else:
        quiz_req = "оптимальну кількість тестових питань (від 4 до 10)"

    retry_section = ""
    if validation_feedback:
        safe_feedback = escape_untrusted_text(validation_feedback)
        retry_section = (
            "\nУВАГА — ПОПЕРЕДНЯ СПРОБА НЕ ПРОЙШЛА ВАЛІДАЦІЮ. ВИПРАВ ПОМИЛКУ:\n"
            f"<<<VALIDATION_ERROR>>>{safe_feedback}<<<END_VALIDATION_ERROR>>>\n"
        )

    safe_topic = escape_untrusted_text(topic or "Загальна тема")
    safe_material = escape_untrusted_text(material)

    user_prompt = (
        tpl.user_template
        .replace("{{topic}}", safe_topic)
        .replace("{{card_requirement}}", card_req)
        .replace("{{quiz_requirement}}", quiz_req)
        .replace("{{retry_section}}", retry_section)
        .replace("{{material}}", safe_material)
    )

    meta = {
        "prompt_name": tpl.name,
        "prompt_version": tpl.version,
        "model_role": tpl.model_role,
        "temperature": tpl.temperature,
        "max_tokens": tpl.max_tokens,
        "schema": tpl.schema,
        "topic_chars": len(safe_topic),
        "material_chars": len(safe_material),
        "system_chars": len(tpl.system_template),
        "user_chars": len(user_prompt),
        "has_retry_feedback": bool(validation_feedback),
    }
    logger.info(
        "Context composition: "
        f"template={meta['prompt_name']}@{meta['prompt_version']} "
        f"role={meta['model_role']} schema={meta['schema']} "
        f"system_chars={meta['system_chars']} user_chars={meta['user_chars']} "
        f"material_chars={meta['material_chars']} retry={meta['has_retry_feedback']}"
    )
    return tpl.system_template, user_prompt, meta
