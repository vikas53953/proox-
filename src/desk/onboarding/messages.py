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

PENDING_REASON = "model access aur market-data rights abhi decide nahi hue (G01, G03)"
