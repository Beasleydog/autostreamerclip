simple_analyze_prompt = """Identify and list the distinct **main ideas** occurring in this clip of <STREAMER_NAME>'s stream.

Definition of a main idea
• A continuous segment where the streamer's words, actions, and on-screen content revolve around a single topic, activity, or narrative thread.

For **each** main idea, provide:
1) **Timestamps** - start and end in mm:ss - mm:ss format (cover every second of the clip; no gaps or overlaps).
2) **Title** - a concise summary of the idea.
3) **Detailed description** (around 3 sentences) that explains what happens, what is said, and what is shown on screen. If there is no speech or on-screen text (e.g., pure gameplay or silent video), give a thorough description of *all* on-screen content.
4) **Direct quotes** (around 3 sentences) - list all significant direct quotes from the streamer's speech or visible on-screen text. If quotes are not applicable, leave this section blank. IF ITS A YOUTUBE VIDEO, YOU MUST INCLUDE THE EXACT TITLE OF THE VIDEO YOU ABSOLUTELY MUST

Guidelines
• If the streamer is reacting to a youtube video you MUST INCLUDE THE EXACT TITLE OF THE VIDEO YOU ABSOLUTELY MUST
• If the streamer STARTS a youtube video, MAKE THAT VERY CLEAR. If the streamer ENDS a youtube video, MAKE THAT VERY CLEAR. IF THEY ARE JUST WATCHING THE VIDEO FROM START TO END, DONT SAY THAT IT STARTS OR ENDS.
• Think EXTREMELY deeply: analyze both what is said and what is shown.
• Do **NOT** merge unrelated ideas; create separate list items.
• This clip is only part of a longer stream that will continue after this clip - never say or imply that the stream is ending.
• Present your answer **exactly** in the numbered template below:

1. [start - end] Title  
   Detailed description…
   Direct quotes…

Before suggesting main ideas, deeply analyze:
1. What are the main distinct activities <STREAMER_NAME> is doing throughout this clip?
2. Where does each activity clearly start and end?
3. Which activities constitute "content" vs just chat/downtime/transitions?
4. How long does each content activity last?
5. Should short interruptions be included or excluded from segments? (the answer is probably included)

If a HISTORY section is supplied, use it as additional context; otherwise, focus solely on this clip.
NOTE THAT IF YOU'RE UNSURE THE NAME OF THE GAME THAT THE STREAMER IS CURRENTLY PLAYING, LOOK TO THE HISTORY IT MAY MENTION IT.
"""

history_prompt = """here is what has been happening in the stream previously, use it as context for your analysis:
<HISTORY>
"""

def build_simple_analyze_prompt(streamer_name: str, history: str, category: str | None = None):
    """Construct the prompt for analyzing a clip.

    Args:
        streamer_name: Display name of the streamer.
        history: Previously generated analysis history to provide extra context.
        category: (Optional) Current Twitch category/game of the streamer which can
            give the model additional context (e.g., the game being played).
    """

    prompt = simple_analyze_prompt.replace("<STREAMER_NAME>", streamer_name)

    # Append current category context if supplied
    if category:
        prompt += f"""This is the current category of the stream.
        If the streamer is playing a game you don't recognize, you should use this as context. (YOU SHOULD PROBABLY MENTION THE GAME NAME IN YOUR OUTPUT IF THIS IS THE CASE)
        You don't need to consider this, but you can.
        Current stream category: {category}"""

    # Append history context if available
    if history:
        prompt += history_prompt.replace("<HISTORY>", history)

    return prompt

# simple_analyze_prompt = """You are an expert content curator for xQc's YouTube channel. Your task is to analyze this video clip and identify the main content activities that should become full YouTube segments. Focus on complete activity blocks, not highlight moments.

# ## CRITICAL THINKING PROCESS (Internal - Do Not Output):
# Before suggesting segments, deeply analyze:
# 1. What are the main distinct activities xQc is doing throughout this clip?
# 2. Where does each activity clearly start and end?
# 3. Which activities constitute "content" vs just chat/downtime/transitions?
# 4. How long does each content activity last?
# 5. Should short interruptions be included or excluded from segments?

# ## REFLECTION & THEMATIC CONNECTION ANALYSIS (Internal - Do Not Output):
# After identifying initial segments, CRITICALLY REFLECT:
# 1. Are there segments that are actually connected by a larger theme or investigation?
# 2. Is xQc researching/exploring a single topic across multiple platforms (YouTube → Reddit → Google → Twitter)?
# 3. Is he on a rant or discussion that spans multiple activities but maintains the same core subject?
# 4. Are there "related activities" that should be combined because they're part of one larger narrative?
# 5. Does combining certain segments create a more coherent story or investigation?

# EXAMPLES OF CONNECTED SEGMENTS TO COMBINE:
# - Research rants: xQc watches a drama video → checks Reddit threads → looks up more info → watches response videos (ALL part of one drama investigation)
# - Game research: Looking up game reviews → watching gameplay → checking Reddit discussions → trying the game (ALL part of deciding whether to play)
# - Topic exploration: Watches news video → checks Twitter reactions → reads articles → discusses with chat (ALL part of one news topic)

# ## CONTENT ACTIVITIES TO IDENTIFY:
# - Watching TikToks/social media (full viewing sessions)
# - Watching YouTube videos (include exact video titles if visible/mentioned)
# - Playing specific games (full gameplay sessions, include game names)
# - Reacting to specific content (police chases, drama videos, etc. - include what he's watching)
# - Extended discussions about specific topics
# - IRL activities that have entertainment value
# - **Research/investigation sessions (even if they span multiple platforms)**

# ## NON-CONTENT TO EXCLUDE:
# - Brief chat interactions between activities
# - Bathroom breaks, food breaks, short tangents
# - Technical difficulties or stream setup
# - Very short transitions between main activities

# ## SEGMENT IDENTIFICATION RULES:
# - Each segment should represent a complete content activity OR thematically connected activities (minimum 2:30, but can be much longer)
# - Include the full duration of the activity, not just highlights
# - If he briefly pauses or interacts with chat during an activity, include it in the segment
# - **CRUCIAL: If multiple activities are connected by the same topic/theme/investigation, combine them into one segment**
# - Only break segments when he definitively moves to a completely different type of content or topic
# - For reaction content, be very specific about what he's reacting to

# ## OUTPUT FORMAT:
# For each content segment, provide:

# SEGMENT_START: [mm:ss]
# SEGMENT_END: [mm:ss]
# SEGMENT_TITLE: [Descriptive title - for reactions include specific content titles, for gaming include game name, for investigations describe the topic, examples: "xQc Investigates [Drama Topic]", "xQc Reacts to 'Daily Dose of Internet'", "xQc plays Minecraft"]
# CONTENT_TYPE: [TikToks, YouTube Reaction, Gaming, Live Reaction, Investigation/Research, etc.]
# DESCRIPTION: [DETAILED description of what xQc is doing during this entire segment - be specific about games played, videos watched, or topics investigated. If it's a connected segment, explain the overarching theme]

# ## EXAMPLES OF GOOD SEGMENTS:
# - 15 minutes of TikTok watching = "xQc Reacts to TikTok Compilation"
# - 25 minutes playing Minecraft = "xQc plays Minecraft"  
# - 8 minutes watching a specific YouTube video = "xQc Reacts to '[Exact Video Title]'"
# - 30 minutes investigating drama across YouTube + Reddit + Twitter = "xQc Investigates [Drama Topic]"
# - 20 minutes researching a game across reviews + gameplay + discussions = "xQc Researches [Game Name]"

# ## QUALITY STANDARDS:
# - Focus on sustained content activities, not brief moments
# - **CRITICALLY IMPORTANT: Recognize when separate activities are actually part of one larger investigation or theme**
# - Be specific about what content he's consuming (video titles, game names, topics investigated)
# - IF THE STREAMER IS WATCHING A VIDEO, YOU MUST INCLUDE THE EXACT TITLE OF THE VIDEO YOU ABSOLUTELY MUST
# - Include natural stopping points (when he definitively switches topics/activities)
# - Don't create segments for pure chat/downtime unless it's substantial discussion about a specific topic
# - Err on the side of longer, thematically coherent segments

# Analyze this xQc clip and identify the main content activities that should become complete YouTube segments, paying special attention to thematic connections between activities."""

# history_prompt = """here is what has been happening in the stream so far, use it as context for your analysis. Some main ideas may be continued, or they may not. DONT OVERFOCUS ON THIS HISTORY, JUST USE IT AS CONTEXT:
# <past_history>
# <HISTORY>
# </past_history>
# """

# def build_simple_analyze_prompt(streamer_name: str, history: str):
#     if history:
#         return simple_analyze_prompt.replace("<STREAMER_NAME>", streamer_name)+history_prompt.replace("<HISTORY>", history)
#     else:
#         return simple_analyze_prompt.replace("<STREAMER_NAME>", streamer_name)