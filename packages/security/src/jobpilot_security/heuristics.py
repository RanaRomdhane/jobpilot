"""Heuristic prompt-injection detection (FR / EN / AR).

Rules run on *folded* text (lowercase, accents and Arabic diacritics stripped), so patterns are
written without accents. Each rule has a weight in (0, 1]; matched weights combine as independent
evidence: score = 1 - Π(1 - w). Weights were chosen so that one strong signal (an explicit
instruction override) flags on its own, while weak signals (e.g. "act as") need corroboration.
"""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass
from enum import StrEnum

from jobpilot_security.text import fold


class Category(StrEnum):
    INSTRUCTION_OVERRIDE = "instruction_override"
    OUTPUT_MANIPULATION = "output_manipulation"
    ROLE_PLAY = "role_play"
    SYSTEM_IMPERSONATION = "system_impersonation"
    TOOL_MENTION = "tool_mention"
    EXFILTRATION = "exfiltration"
    ENCODING = "encoding"
    HIDDEN_CONTENT = "hidden_content"


@dataclass(frozen=True, slots=True)
class Rule:
    id: str
    category: Category
    weight: float
    pattern: re.Pattern[str]
    negatable: bool = False  # "never share your password" is advice, not an attack


@dataclass(frozen=True, slots=True)
class Match:
    rule: str
    category: Category
    weight: float
    excerpt: str


def _r(
    id_: str, category: Category, weight: float, pattern: str, *, negatable: bool = False
) -> Rule:
    return Rule(id_, category, weight, re.compile(pattern, re.IGNORECASE | re.UNICODE), negatable)


# a negation shortly before the match turns a demand into advice ("never share your password")
_NEGATION = re.compile(
    r"\b(?:never|don.?t|do not|must not|should not|shouldn.?t|jamais|ne (?:\w+ )?pas|"
    r"n.(?:\w+ )?pas|لا)\b[^.!?\n]{0,40}$"
)


_INSTR_EN = r"(?:instructions?|prompts?|rules|directives|guidelines|system message|context)"
_INSTR_FR = r"(?:instructions?|consignes?|regles?|directives?|prompts?|ordres?)"

RULES: tuple[Rule, ...] = (
    # --- instruction override
    _r(
        "en.ignore_instructions",
        Category.INSTRUCTION_OVERRIDE,
        0.9,
        rf"\b(?:ignore|disregard|forget|override|bypass|skip)\b(?:\W+\w+){{0,4}}?\W+"
        rf"(?:previous|prior|above|earlier|preceding|your|all|any|the|these|those|system)\b"
        rf"(?:\W+\w+){{0,3}}?\W+{_INSTR_EN}\b",
    ),
    _r(
        "en.new_instructions",
        Category.INSTRUCTION_OVERRIDE,
        0.7,
        rf"\b(?:new|updated|real|actual|following)\s+{_INSTR_EN}\s*(?::|follow|apply)",
    ),
    _r(
        "fr.ignore_instructions",
        Category.INSTRUCTION_OVERRIDE,
        0.9,
        rf"\b(?:ignore[rsz]?|oublie[rsz]?|ne tiens? (?:pas|plus) compte d?e?s?|neglige[rsz]?|"
        rf"fais abstraction d?e?s?|contourne[rsz]?)\b(?:\W+\w+){{0,4}}?\W+{_INSTR_FR}\b",
    ),
    _r(
        "fr.new_instructions",
        Category.INSTRUCTION_OVERRIDE,
        0.7,
        rf"\b(?:nouvelles?|vraies?|veritables?)\s+{_INSTR_FR}\s*:",
    ),
    _r(
        "ar.ignore_instructions",
        Category.INSTRUCTION_OVERRIDE,
        0.9,
        r"(?:تجاهل|انس|اهمل|لا تلتزم ب)\s*(?:\S+\s+){0,3}?(?:التعليمات|الاوامر|التوجيهات|القواعد)",
    ),
    # --- manipulation of *our* outputs (classification, scoring, ranking)
    _r(
        "en.classify_as",
        Category.OUTPUT_MANIPULATION,
        0.85,
        r"\b(?:classify|categori[sz]e|label|tag|mark|flag|treat|rank|score|rate)\s+"
        r"(?:this|the|that|it|me)\b(?:\W+\w+){0,3}?\W+as\b",
    ),
    _r(
        "en.must_output",
        Category.OUTPUT_MANIPULATION,
        0.6,
        r"\b(?:you must|always|only) (?:respond|answer|reply|output|return)\b",
    ),
    _r(
        "fr.classify_as",
        Category.OUTPUT_MANIPULATION,
        0.85,
        r"\b(?:classe[rsz]?|categorise[rsz]?|etiquete[rsz]?|marque[rsz]?|considere[rsz]?|"
        r"note[rsz]?)\s+(?:cet|ce|cette|ceci|le|la|l)\b(?:\W+\w+){0,3}?\W+(?:comme|en tant que)\b",
    ),
    _r(
        "fr.must_output",
        Category.OUTPUT_MANIPULATION,
        0.6,
        r"\b(?:tu dois|vous devez) (?:repondre|renvoyer|retourner|afficher)\b",
    ),
    _r("ar.classify_as", Category.OUTPUT_MANIPULATION, 0.85, r"(?:صنف|اعتبر)\s+(?:هذا|هذه)"),
    # --- role play / persona switch
    _r(
        "en.you_are_now",
        Category.ROLE_PLAY,
        0.6,
        r"\b(?:you are now|from now on,? you|you will now act|pretend (?:to be|you are)|"
        r"roleplay as|switch to .{0,20}mode)\b",
    ),
    _r("en.act_as", Category.ROLE_PLAY, 0.25, r"\bact as (?:an?|the|my)\b"),
    _r(
        "fr.tu_es_maintenant",
        Category.ROLE_PLAY,
        0.6,
        r"\b(?:tu es (?:desormais|maintenant)|vous etes (?:desormais|maintenant)|a partir de "
        r"maintenant,? (?:tu|vous)|fais semblant d.etre|joue[rsz]? le role)\b",
    ),
    _r("ar.you_are_now", Category.ROLE_PLAY, 0.6, r"(?:انت الان|من الان فصاعدا)"),
    # --- system / developer impersonation and chat-template tokens
    _r(
        "system.template_tokens",
        Category.SYSTEM_IMPERSONATION,
        0.85,
        r"<\|(?:im_start|im_end|system|start|end|channel|message)\|>|\[/?inst\]|<</?sys>>",
    ),
    _r(
        "system.role_header",
        Category.SYSTEM_IMPERSONATION,
        0.6,
        r"(?:^|\n)\s*(?:#{1,4}\s*)?(?:system|assistant|developer|admin)\s*(?:prompt|message)?\s*:",
    ),
    _r(
        "system.authority",
        Category.SYSTEM_IMPERSONATION,
        0.6,
        r"\b(?:message|note|instruction|notice) (?:from|du|de la|de l) "
        r"(?:system|systeme|admin(?:istrator|istrateur)?|developer|developpeur|openai|anthropic|"
        r"groq|jobpilot)\b",
    ),
    _r(
        "system.jailbreak",
        Category.SYSTEM_IMPERSONATION,
        0.6,
        r"\b(?:jailbreak|developer mode|dan mode|mode developpeur|do anything now)\b",
    ),
    # --- mentions of our tools or of tool calling
    _r(
        "tool.names",
        Category.TOOL_MENTION,
        0.8,
        r"\b(?:bulk_classify_and_label|get_emails_compact|list_unprocessed_emails|get_master_profile|"
        r"get_job|get_template_schema|send_email|send_message|delete_email|trash_message)\b",
    ),
    _r(
        "tool.generic",
        Category.TOOL_MENTION,
        0.3,  # product newsletters talk about "tool calls"; needs corroboration to flag
        r"\b(?:call|invoke|use|execute|appelle[rz]?|utilise[rz]?) (?:the |a |le |la |l.)?"
        r"(?:tool|function|outil|fonction)\b|\b(?:function|tool)[ _]call\b",
    ),
    # --- exfiltration / unwanted actions
    _r(
        "exfil.send_secrets",
        Category.EXFILTRATION,
        0.8,
        r"\b(?:send|forward|email|reveal|print|share|envoie[rsz]?|transmets?|transfere[rsz]?|"
        r"revele[rsz]?)\b.{0,60}\b(?:password|api[ _-]?key|token|credentials?|secret|"
        r"mot de passe|identifiants?|system prompt|cle api)\b",
        negatable=True,
    ),
    _r(
        "exfil.forward_all",
        Category.EXFILTRATION,
        0.75,
        r"\b(?:forward|send|transfere[rsz]?|envoie[rsz]?) (?:all|every|tous|toutes)\b.{0,30}"
        r"\b(?:emails?|messages?|mails?|courriels?)\b",
        negatable=True,
    ),
)

# URLs are matched as a placeholder: query strings ("verify-email?token=…") are not instructions
_URL = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_BASE64 = re.compile(r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{80,}={0,2}(?![A-Za-z0-9+/])")


def _excerpt(text: str, start: int, end: int, width: int = 40) -> str:
    a, b = max(0, start - width), min(len(text), end + width)
    return ("…" if a else "") + text[a:b].replace("\n", " ") + ("…" if b < len(text) else "")


def _decode_blob(blob: str) -> str | None:
    try:
        decoded = base64.b64decode(blob + "=" * (-len(blob) % 4)).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None
    printable = sum(ch.isprintable() or ch.isspace() for ch in decoded)
    return decoded if decoded and printable / len(decoded) > 0.95 else None


def match_rules(text: str, *, decode: bool = True) -> list[Match]:
    folded = _URL.sub(" [url] ", fold(text))
    found: list[Match] = []
    for rule in RULES:
        for m in rule.pattern.finditer(folded):
            if rule.negatable and _NEGATION.search(folded[max(0, m.start() - 60) : m.start()]):
                continue
            found.append(Match(rule.id, rule.category, rule.weight, _excerpt(folded, *m.span())))
            break
    for m in _BASE64.finditer(text):
        found.append(
            Match(
                "encoding.base64_blob",
                Category.ENCODING,
                # opaque blobs are everywhere in real mail (tracking tokens, data URIs): weak on
                # their own; a blob that decodes to an instruction scores through `decoded.*`
                0.1,
                _excerpt(text, m.start(), min(m.end(), m.start() + 40), 10),
            )
        )
        decoded = _decode_blob(m.group()) if decode else None
        if decoded:
            # an encoded instruction is an attempt to slip past filters: score it with a boost
            for inner in match_rules(decoded, decode=False):
                found.append(
                    Match(
                        f"decoded.{inner.rule}",
                        inner.category,
                        min(1.0, inner.weight + 0.1),
                        inner.excerpt,
                    )
                )
        break
    return found


def combine(weights: list[float]) -> float:
    remaining = 1.0
    for w in weights:
        remaining *= 1.0 - max(0.0, min(1.0, w))
    return round(1.0 - remaining, 4)
