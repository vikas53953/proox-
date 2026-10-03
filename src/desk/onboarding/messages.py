"""Reply texts, from the Design doc v1.0 template drafts (Roman Hinglish first).

These are free-form replies inside the 24-hour service window. They are NOT approved
Meta templates; proactive sends outside the window wait for G02 template approval.
"""

WELCOME_PENDING = (
    "Invite verified. Nifty aur large caps se start karenge. "
    "Main research deta hoon, trades place nahi karta.\n"
    "Research setup abhi pending hai: {reason}. Abhi live feed ya live answer ka claim nahi."
)
OPT_IN = (
    "Roz valid market day par 08:45 IST brief chahiye? YES ya NO. "
    "STOP se daily updates pause honge."
)
OPT_IN_YES = "Theek hai. Valid trading days par 08:45 IST brief milega. STOP se pause kar sakte ho."
OPT_IN_NO = "Theek hai, daily brief off. Sawal kabhi bhi pooch sakte ho. START se wapas on."
STOP = (
    "Daily updates paused; questions ab bhi pooch sakte ho. "
    "START se preferences confirm karke resume karenge."
)
START = "Daily 08:45 IST brief resume karein? YES ya NO."
QUESTION_PENDING = (
    "Sawal mil gaya. Research setup pending hai: {reason}. "
    "Abhi jawab ka claim nahi karunga; setup ready hote hi bataunga."
)
TEXT_ONLY = "Abhi sirf text messages samajh sakta hoon (voice setup pending)."
# One neutral text for every unknown / expired / used / forwarded / wrong-number case,
# so the reply never reveals which case applied or anything about another tenant.
NEUTRAL = (
    "Ye invite valid nahi hai ya expire ho gaya hai. Jisne invite bheja, unse naya invite maangiye."
)

NO_REPORT_TODAY = "Aaj ka report abhi tak nahi bana. Purana report aaj ka bata kar nahi bhejenge."

PENDING_REASON = "model access aur market-data rights abhi decide nahi hue (G01, G03)"

# ---- DRAFT: Telegram privacy disclosure (GATES.md T1) ------------------------------------
# STATUS: DRAFT — wording NOT approved. The owner must approve it before any user other
# than the owner is invited on Telegram. Sent only when TELEGRAM_PRIVACY_NOTICE=1 (off by
# default), once per tenant, right after the Telegram welcome. Roman Hinglish first.
PRIVACY_NOTICE_STATUS = "DRAFT"
PRIVACY_NOTICE_TELEGRAM_DRAFT = (
    "Privacy: Telegram bot chats end-to-end encrypted nahi hote, aur Telegram unhe apne "
    "servers par store karta hai. {stored_hi} Kabhi bhi STOP likho, daily updates ruk "
    "jayenge.\n"
    "Privacy: Telegram bot chats are not end-to-end encrypted, and Telegram stores them on "
    "its servers. {stored_en} Reply STOP anytime to pause daily updates."
)
# The chat-id sentence depends on B02 (DESK_ENCRYPT_SENDERS): never claim encryption when
# it is off.
PRIVACY_STORED_ENCRYPTED = (
    "Desk aapka chat id encrypted form mein store karta hai.",
    "The desk stores your chat id encrypted.",
)
PRIVACY_STORED_PLAIN = (
    "Desk aapka chat id store karta hai taaki brief bhej sake.",
    "The desk stores your chat id so it can send you the brief.",
)


def privacy_notice_telegram(encrypted: bool) -> str:
    hi, en = PRIVACY_STORED_ENCRYPTED if encrypted else PRIVACY_STORED_PLAIN
    return PRIVACY_NOTICE_TELEGRAM_DRAFT.format(stored_hi=hi, stored_en=en)
