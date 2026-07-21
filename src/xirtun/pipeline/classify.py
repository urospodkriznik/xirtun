"""Classify an inbound message into an intent: meal | symptom | other."""

from __future__ import annotations

from xirtun.llm.base import LLMClient
from xirtun.pipeline.models import Intent, QAReplyRouting

CLASSIFY_SYSTEM = (
    "You triage messages sent to a personal nutrition assistant. "
    "Classify the user's message into exactly one intent:\n"
    "- 'meal': they are describing food or drink they consumed.\n"
    "- 'symptom': they are reporting a physical symptom or how they feel.\n"
    "- 'exercise': they are reporting a specific workout/activity they did (e.g. "
    "'ran 5k this morning', 'did legs at the gym for 45 min').\n"
    "- 'note': they are sharing a goal, preference, routine, exercise habit, or a "
    "fact/idea to remember (e.g. 'I want to gain muscle', 'I exercise twice a week', "
    "'I want more lutein in my diet').\n"
    "- 'shopping': they are asking what to buy or for help planning groceries.\n"
    "- 'food': they are saving/registering a food's nutrition facts (e.g. 'save Lidl "
    "vegan sausage: per 100g 250 kcal, 18g protein, 12g fat, 4g carbs').\n"
    "- 'other': anything else (questions, chit-chat).\n"
    "Respond using the provided schema."
)


def classify(llm: LLMClient, text: str) -> str:
    messages = [
        {"role": "system", "content": CLASSIFY_SYSTEM},
        {"role": "user", "content": text},
    ]
    response = llm.complete(messages, schema=Intent)
    return response.data["intent"]


_QA_REPLY_SYSTEM = (
    "The user was just asked follow-up questions by their weekly nutrition review and "
    "sent a reply. Decide what the reply IS:\n"
    "- 'answer': they are responding to, reacting to, or commenting on the questions — "
    "even partially, even loosely, and EVEN IF the reply mentions food, hunger, or "
    "symptoms (the questions are often ABOUT those, so an answer naturally discusses "
    "them). Reflections like 'I don't feel hungry lately, my belly feels bloated, maybe "
    "smoothies would help' are ANSWERS.\n"
    "- 'new_log': a brand-new diary entry (a specific meal/workout/symptom occurrence) "
    "that is clearly UNRELATED to the questions and meant to be recorded — e.g. asked "
    "about sleep, they reply 'just ate chicken and rice'.\n"
    "When it could be either, choose 'answer' — they are in the middle of answering. "
    "Respond using the provided schema."
)


def classify_qa_reply(llm: LLMClient, questions: list[str], text: str) -> str:
    """Context-aware routing for a reply sent while weekly-review questions are pending.
    Returns 'answer' or 'new_log'. Unlike `classify`, it sees the questions, so a reply
    that discusses symptoms because it's ANSWERING a symptom question isn't mistaken for
    a new symptom log (the bug this exists to fix)."""
    asked = "\n".join(f"{i}. {q}" for i, q in enumerate(questions, start=1))
    messages = [
        {"role": "system", "content": _QA_REPLY_SYSTEM},
        {"role": "user", "content": f"Questions asked:\n{asked}\n\nTheir reply:\n{text}"},
    ]
    response = llm.complete(messages, schema=QAReplyRouting)
    return response.data["kind"]
