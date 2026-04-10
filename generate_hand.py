"""
generate_hand.py

Generates a daily poker hand-reading puzzle for "Read the Hand".

Quality improvements:
- Retries until the opponent has a real pair (hole card involved, not just board pair)
- Per-street hand analysis drives accurate narrative templates
- LLM prompt includes exact per-street hand facts with explicit "do not contradict" rules
- Wrong reasons are fact-driven (reference the actual key action, not random templates)
- Wrong choices filtered to avoid duplicate labels

Run:   python generate_hand.py
Output: daily_hand.json
"""

import json, os, random, datetime, requests, re

# ─── Config ──────────────────────────────────────────────
GROQ_API_KEY = os.environ.get('GROQ_API_KEY', '')
GROQ_MODEL   = 'llama-3.3-70b-versatile'

# ─── Card constants ──────────────────────────────────────
SUITS  = ['s', 'h', 'd', 'c']
RANKS  = ['2','3','4','5','6','7','8','9','T','J','Q','K','A']
RANK_DISPLAY = {'T':'10','J':'J','Q':'Q','K':'K','A':'A'}
RANK_VAL = {r: i for i, r in enumerate(RANKS)}

def display(rank): return RANK_DISPLAY.get(rank, rank)

def fmt_card(c):
    return f"{display(c['rank'])}{'♠♥♦♣'['shdc'.index(c['suit'])]}"

def rank_name(r):
    return {'T':'10','J':'Jack','Q':'Queen','K':'King','A':'Ace'}.get(r, r)

def card_label(cards):
    return f"{display(cards[0]['rank'])} {display(cards[1]['rank'])}"


# ─── Deck ────────────────────────────────────────────────
def create_deck():
    return [{'rank': r, 'suit': s} for r in RANKS for s in SUITS]

def deal(deck, n):
    cards = deck[:n]; del deck[:n]; return cards


# ─── Hand evaluation ─────────────────────────────────────
def hand_name(hole, community):
    cards = hole + community
    ranks = [RANK_VAL[c['rank']] for c in cards]
    suits = [c['suit'] for c in cards]
    rc = {}
    for r in ranks: rc[r] = rc.get(r, 0) + 1
    sc = {}
    for s in suits: sc[s] = sc.get(s, 0) + 1
    counts = sorted(rc.values(), reverse=True)
    has_flush = any(v >= 5 for v in sc.values())
    ur = sorted(set(ranks))
    has_str = any(ur[i+4]-ur[i]==4 and len(set(ur[i:i+5]))==5 for i in range(len(ur)-4))
    if not has_str and 12 in ur and all(r in ur for r in [0,1,2,3]): has_str = True
    if has_str and has_flush: return 'Straight Flush'
    if counts[0] == 4:       return 'Four of a Kind'
    if counts[0]==3 and len(counts)>1 and counts[1]>=2: return 'Full House'
    if has_flush:            return 'Flush'
    if has_str:              return 'Straight'
    if counts[0] == 3:       return 'Three of a Kind'
    if counts[0]==2 and len(counts)>1 and counts[1]==2: return 'Two Pair'
    if counts[0] == 2:       return 'One Pair'
    return 'High Card'

HAND_RANK = {
    'High Card':0,'One Pair':1,'Two Pair':2,'Three of a Kind':3,
    'Straight':4,'Flush':5,'Full House':6,'Four of a Kind':7,'Straight Flush':8,
}


# ─── Per-street opponent analysis ────────────────────────
def opp_analysis(opp_cards, community):
    """
    Compute per-street hand strength and improvement flags.
    This is the single source of truth used by narrative, LLM prompt, and wrong reasons.
    """
    opp_rv  = [RANK_VAL[c['rank']] for c in opp_cards]
    flop_rv = [RANK_VAL[c['rank']] for c in community[:3]]

    flop_h  = hand_name(opp_cards, community[:3])
    turn_h  = hand_name(opp_cards, community[:4])
    river_h = hand_name(opp_cards, community)

    pocket_pair    = opp_rv[0] == opp_rv[1]
    # A hole card actually pairs a board card (not just riding a board pair)
    pairs_flop     = pocket_pair or any(rv in flop_rv for rv in opp_rv)
    improved_turn  = HAND_RANK[turn_h]  > HAND_RANK[flop_h]
    improved_river = HAND_RANK[river_h] > HAND_RANK[turn_h]
    # Pocket pair that beats every flop card
    overpair       = pocket_pair and min(opp_rv) > max(flop_rv)

    return {
        'flop_h': flop_h,
        'turn_h': turn_h,
        'river_h': river_h,
        'pocket_pair': pocket_pair,
        'pairs_flop': pairs_flop,
        'overpair': overpair,
        'improved_turn': improved_turn,
        'improved_river': improved_river,
    }


# ─── Quality filter ───────────────────────────────────────
def is_quality_hand(opp_cards, community):
    """
    Accept Two Pair or better unconditionally.
    For One Pair, require that a hole card is actually involved in the pair —
    pocket pair, or a hole card that matches a board card. This rejects hands
    where the 'pair' is purely a board pair the opponent is just riding (e.g.
    Q-9 on T-T-7 board), which have no coherent narrative.
    """
    h  = hand_name(opp_cards, community)
    hr = HAND_RANK[h]
    if hr >= HAND_RANK['Two Pair']:
        return True
    if hr == HAND_RANK['One Pair']:
        opp_ranks  = {RANK_VAL[c['rank']] for c in opp_cards}
        board_ranks = [RANK_VAL[c['rank']] for c in community]
        # Pocket pair always has a clear narrative
        if len(opp_ranks) == 1:
            return True
        # At least one hole card rank must appear on the board
        return any(r in board_ranks for r in opp_ranks)
    return False


# ─── Narrative templates (hand-type specific) ─────────────
def build_narrative(opp_cards, your_cards, community):
    """
    Returns (events, explanation, ctx) where ctx is the opp_analysis dict.
    All narrative choices are driven by ctx so they accurately reflect what
    the opponent's hand actually was at each street.
    """
    flop  = community[:3]
    turn  = community[3]
    river = community[4]
    fn = ', '.join(rank_name(c['rank']) for c in flop)
    tn = rank_name(turn['rank'])
    rn = rank_name(river['rank'])

    h   = hand_name(opp_cards, community)
    hr  = HAND_RANK[h]
    lbl = card_label(opp_cards)
    ctx = opp_analysis(opp_cards, community)

    your_label = f"{display(your_cards[0]['rank'])}, {display(your_cards[1]['rank'])}"
    your_prefix = f"<strong>Your cards — {your_label}.</strong> "

    if hr >= HAND_RANK['Full House']:
        # Monster — slow play then trap
        events = [
            {'icon':'💰','text':f'{your_prefix}You raised before the cards were dealt. Your opponent called.'},
            {'icon':'🃏','text':f'<strong>Flop</strong> — {fn}. You bet. <strong>Your opponent just called.</strong>'},
            {'icon':'🃏','text':f'<strong>Turn</strong> — {tn}. You bet again. <strong>Your opponent raised you.</strong>'},
            {'icon':'🌊','text':f'<strong>River</strong> — {rn}. Your opponent <strong>moved all-in.</strong>'},
        ]
        explanation = (
            f'<strong>Answer: {lbl}</strong><br><br>'
            f'Your opponent flopped a {h.lower()} and slow-played the flop, calling to disguise their strength. '
            f'Once the pot was big enough they raised the turn and shoved the river — a classic trap with a monster hand.'
        )

    elif hr == HAND_RANK['Flush']:
        events = [
            {'icon':'💰','text':f'{your_prefix}Your opponent called your raise before the cards were dealt.'},
            {'icon':'🃏','text':f'<strong>Flop</strong> — {fn}. You bet. <strong>Your opponent called.</strong>'},
            {'icon':'🃏','text':f'<strong>Turn</strong> — {tn}. You checked. <strong>Your opponent bet.</strong>'},
            {'icon':'🌊','text':f'<strong>River</strong> — {rn}. Your opponent <strong>bet large — about the size of the pot.</strong>'},
        ]
        explanation = (
            f'<strong>Answer: {lbl}</strong><br><br>'
            f'Your opponent was drawing to a flush and called the flop to keep their options open. '
            f'Once they hit, they bet the turn and river for value — growing aggression on later streets is a hallmark of a completed draw.'
        )

    elif hr == HAND_RANK['Straight']:
        events = [
            {'icon':'💰','text':f'{your_prefix}Both players called a raise before the cards were dealt.'},
            {'icon':'🃏','text':f'<strong>Flop</strong> — {fn}. You bet. <strong>Your opponent raised.</strong>'},
            {'icon':'🃏','text':f'<strong>Turn</strong> — {tn}. Your opponent led out with a bet. <strong>You called.</strong>'},
            {'icon':'🌊','text':f'<strong>River</strong> — {rn}. Your opponent <strong>fired again — half the pot.</strong>'},
        ]
        explanation = (
            f'<strong>Answer: {lbl}</strong><br><br>'
            f'Your opponent flopped a straight and immediately raised to build the pot. '
            f'They led every remaining street — consistent value betting across all streets is a strong signal of a made hand.'
        )

    elif hr == HAND_RANK['Three of a Kind']:
        events = [
            {'icon':'💰','text':'You raised before the cards were dealt. Your opponent called.'},
            {'icon':'🃏','text':f'<strong>Flop</strong> — {fn}. You bet. <strong>Your opponent raised you back.</strong>'},
            {'icon':'🃏','text':f'<strong>Turn</strong> — {tn}. Your opponent bet out. <strong>You called.</strong>'},
            {'icon':'🌊','text':f'<strong>River</strong> — {rn}. Your opponent <strong>shoved all-in without hesitation.</strong>'},
        ]
        explanation = (
            f'<strong>Answer: {lbl}</strong><br><br>'
            f'Your opponent flopped three of a kind and raised the flop immediately to build the pot. '
            f'Sets are too strong to slow-play — they pushed their advantage on every street.'
        )

    elif hr == HAND_RANK['Two Pair']:
        if ctx['improved_turn']:
            # Had one pair on the flop, picked up two pair on the turn
            events = [
                {'icon':'💰','text':'You raised before the cards were dealt. Your opponent called.'},
                {'icon':'🃏','text':f'<strong>Flop</strong> — {fn}. You bet. <strong>Your opponent called.</strong>'},
                {'icon':'🃏','text':f'<strong>Turn</strong> — {tn}. You checked. <strong>Your opponent bet — bigger than expected.</strong>'},
                {'icon':'🌊','text':f'<strong>River</strong> — {rn}. Your opponent <strong>fired again for value.</strong>'},
            ]
            explanation = (
                f'<strong>Answer: {lbl}</strong><br><br>'
                f'Your opponent called the flop with one pair, then picked up two pair on the turn. '
                f'The sudden aggression on the turn — after passivity on the flop — is the tell: they improved and immediately started building the pot.'
            )
        else:
            # Had two pair from the flop, bet all streets
            events = [
                {'icon':'💰','text':f'{your_prefix}Your opponent raised before the cards were dealt. You called.'},
                {'icon':'🃏','text':f'<strong>Flop</strong> — {fn}. Your opponent bet. <strong>You called.</strong>'},
                {'icon':'🃏','text':f'<strong>Turn</strong> — {tn}. <strong>Your opponent bet again — bigger.</strong>'},
                {'icon':'🌊','text':f'<strong>River</strong> — {rn}. Your opponent <strong>fired a large river bet.</strong>'},
            ]
            explanation = (
                f'<strong>Answer: {lbl}</strong><br><br>'
                f'Your opponent flopped two pair and bet all three streets with growing sizing. '
                f'The consistent pressure — especially the larger turn bet — signals a hand strong enough to protect but not quite a monster.'
            )

    elif hr == HAND_RANK['One Pair'] and ctx['overpair']:
        events = [
            {'icon':'💰','text':f'{your_prefix}Your opponent raised before the cards were dealt. You called.'},
            {'icon':'🃏','text':f'<strong>Flop</strong> — {fn}. Your opponent bet. <strong>You called.</strong>'},
            {'icon':'🃏','text':f'<strong>Turn</strong> — {tn}. Your opponent bet again. <strong>You called.</strong>'},
            {'icon':'🌊','text':f'<strong>River</strong> — {rn}. <strong>Your opponent checked — then called your bet.</strong>'},
        ]
        explanation = (
            f'<strong>Answer: {lbl}</strong><br><br>'
            f'Your opponent had an overpair and bet the flop and turn for value but checked back the river — '
            f'a sign they were confident earlier but wanted pot control once the board got scarier.'
        )

    elif hr == HAND_RANK['One Pair'] and ctx['pairs_flop']:
        # Hit their pair on the flop — passive call, check turn, lead river
        events = [
            {'icon':'💰','text':'You raised before the cards were dealt. Your opponent called.'},
            {'icon':'🃏','text':f'<strong>Flop</strong> — {fn}. Your opponent checked. You bet. <strong>Your opponent called.</strong>'},
            {'icon':'🃏','text':f'<strong>Turn</strong> — {tn}. Both players checked.'},
            {'icon':'🌊','text':f'<strong>River</strong> — {rn}. Your opponent <strong>led out with a bet.</strong>'},
        ]
        explanation = (
            f'<strong>Answer: {lbl}</strong><br><br>'
            f'Your opponent flopped a pair but played it passively — calling the flop and checking the turn — '
            f'before leading the river to extract thin value. The check-call-check-lead line is a tell for a medium-strength made hand.'
        )

    elif hr == HAND_RANK['One Pair'] and ctx['improved_turn']:
        # Missed the flop entirely, hit their pair on the turn card
        events = [
            {'icon':'💰','text':'You raised before the cards were dealt. Your opponent called.'},
            {'icon':'🃏','text':f'<strong>Flop</strong> — {fn}. You bet. <strong>Your opponent called</strong>, staying patient.'},
            {'icon':'🃏','text':f'<strong>Turn</strong> — {tn}. You bet. <strong>Your opponent raised</strong> — they just connected.'},
            {'icon':'🌊','text':f'<strong>River</strong> — {rn}. Your opponent <strong>bet again when checked to.</strong>'},
        ]
        explanation = (
            f'<strong>Answer: {lbl}</strong><br><br>'
            f'Your opponent called the flop with overcards, then the turn card gave them a pair. '
            f'The raise on the turn — right after that card landed — is the tell: passive until they hit, then immediately aggressive.'
        )

    else:
        # One Pair picked up on the river — called two streets, then led river
        events = [
            {'icon':'💰','text':'You raised before the cards were dealt. Your opponent called.'},
            {'icon':'🃏','text':f'<strong>Flop</strong> — {fn}. You bet. <strong>Your opponent called.</strong>'},
            {'icon':'🃏','text':f'<strong>Turn</strong> — {tn}. You bet. <strong>Your opponent called again.</strong>'},
            {'icon':'🌊','text':f'<strong>River</strong> — {rn}. Your opponent <strong>led out with a confident bet.</strong>'},
        ]
        explanation = (
            f'<strong>Answer: {lbl}</strong><br><br>'
            f'Your opponent called two streets with nothing but overcards, then the river card paired one of their hole cards. '
            f'The sudden lead on the river — after calling twice — is the tell: they finally hit something and went for value.'
        )

    return events, explanation, ctx


# ─── Strategic wrong choices ─────────────────────────────
def generate_wrong_choices(opp_cards, deck_remaining, community):
    """
    Return 3 wrong card pairs: one stronger hand, one similar, one weaker.
    Filters out pairs whose label duplicates the correct answer label.
    """
    correct_label = card_label(opp_cards)
    correct_h     = hand_name(opp_cards, community)
    correct_hr    = HAND_RANK[correct_h]

    available = list(deck_remaining)
    random.shuffle(available)

    stronger, similar, weaker = [], [], []
    for i in range(0, len(available) - 1, 2):
        pair = [available[i], available[i+1]]
        lbl = card_label(pair)
        if lbl == correct_label:
            continue  # skip duplicate labels
        h  = hand_name(pair, community)
        hr = HAND_RANK[h]
        if hr > correct_hr:    stronger.append((pair, h))
        elif hr == correct_hr: similar.append((pair, h))
        else:                  weaker.append((pair, h))

    chosen = []
    for pool in [similar, stronger, weaker]:
        if pool and len(chosen) < 3:
            chosen.append(pool[0])

    # Fill any remaining slots from all pairs
    all_pairs = []
    for i in range(0, len(available) - 1, 2):
        pair = [available[i], available[i+1]]
        if card_label(pair) != correct_label:
            all_pairs.append((pair, hand_name(pair, community)))

    for pair, h in all_pairs:
        if len(chosen) >= 3:
            break
        if not any(pair == c[0] for c in chosen):
            chosen.append((pair, h))

    return chosen[:3]


# ─── Wrong reasons tied to actual game events ─────────────
def build_wrong_reason(wrong_pair, correct_pair, community, ctx):
    """
    Build a wrong reason using ctx (opp_analysis of the correct hand) so
    we reference actions that actually happened in the narrative.
    """
    wl  = card_label(wrong_pair)
    wh  = hand_name(wrong_pair, community)
    ch  = hand_name(correct_pair, community)
    whr = HAND_RANK[wh]
    chr = HAND_RANK[ch]

    if whr > chr:
        # Wrong choice is stronger — would have played bigger/faster
        return (
            f"{wl} makes a {wh.lower()} here — a hand that strong would have raised "
            f"earlier and pushed harder for maximum value. The measured sizing in this hand points to something weaker."
        )
    elif whr < chr:
        # Wrong choice is weaker — wouldn't justify the aggression shown
        if ctx['improved_turn']:
            return (
                f"{wl} doesn't benefit from the turn card the way the correct hand does. "
                f"A player holding only {wh.lower()} wouldn't have the confidence to raise on that street."
            )
        elif ctx['improved_river']:
            return (
                f"{wl} has little reason to lead the river here — {wh.lower()} can't comfortably "
                f"go for value after calling two streets."
            )
        else:
            return (
                f"With only {wh.lower()}, {wl} wouldn't commit chips across multiple streets. "
                f"This betting line requires a genuinely made hand from the flop onward."
            )
    else:
        # Similar strength — subtler distinction based on when the correct hand improved
        if ctx['improved_turn']:
            return (
                f"{wl} has similar strength, but look at when the aggression shifted — "
                f"the turn card connects with the correct hand in a way it doesn't connect with {wl}."
            )
        elif ctx['improved_river']:
            return (
                f"{wl} is close, but the river lead is the tell. "
                f"The correct hand picked up more on the river than {wl} would have."
            )
        else:
            return (
                f"{wl} plays similarly, but the correct hand pairs a specific board card that {wl} doesn't. "
                f"Look at which hole card ranks actually appear on the board."
            )


# ─── LLM upgrade (optional) ──────────────────────────────
def extract_json(text):
    """Extract the first complete JSON object by counting braces, ignoring braces inside strings."""
    start = text.find('{')
    if start == -1:
        raise ValueError('No JSON object found')
    depth, in_string, escape = 0, False, False
    for i, ch in enumerate(text[start:], start):
        if escape:
            escape = False
            continue
        if ch == '\\' and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return text[start:i+1]
    raise ValueError('Unterminated JSON object')


def llm_narrative(opp_cards, your_cards, community, template_events, template_explanation, ctx):
    """
    Enhance the template narrative with Groq's Llama 3.3 70B.
    Passes per-street hand facts explicitly so the LLM cannot contradict them.
    Falls back to template on failure.
    """
    if not GROQ_API_KEY:
        raise ValueError('No GROQ_API_KEY')

    flop, turn, river = community[:3], community[3], community[4]
    fn = ', '.join(rank_name(c['rank']) for c in flop)
    tn, rn = rank_name(turn['rank']), rank_name(river['rank'])
    lbl        = card_label(opp_cards)
    your_label = f"{display(your_cards[0]['rank'])}, {display(your_cards[1]['rank'])}"

    scaffold = '\n'.join(f'- {e["text"]}' for e in template_events)

    # Per-street improvement summary for the prompt
    turn_note  = '← IMPROVED HERE (hole card connected)' if ctx['improved_turn']  else '(no change from flop)'
    river_note = '← IMPROVED HERE (hole card connected)' if ctx['improved_river'] else '(no change from turn)'

    prompt = f"""You write content for a daily poker puzzle game called "Read the Hand".
Players see a play-by-play and must guess what hole cards the opponent held from 4 options.
The narrative MUST be factually consistent with the opponent's actual hand at every street.

EXACT HAND FACTS — do not contradict these:
- Opponent's hole cards: {lbl}
- Their hand after the FLOP ({fn}): {ctx['flop_h']}
- Their hand after the TURN ({tn}): {ctx['turn_h']}  {turn_note}
- Their hand after the RIVER ({rn}): {ctx['river_h']}  {river_note}

TEMPLATE NARRATIVE TO REWRITE (make it vivid and feel like real money on the line):
{scaffold}

TEMPLATE EXPLANATION TO REWRITE:
{template_explanation}

Return ONLY this JSON — no markdown, no extra text:
{{
  "events": [
    {{"icon": "💰", "text": "<strong>Your cards — {your_label}.</strong> pre-flop action in 1-2 sentences"}},
    {{"icon": "🃏", "text": "<strong>Flop</strong> — {fn}. what happened on the flop"}},
    {{"icon": "🃏", "text": "<strong>Turn</strong> — {tn}. what happened on the turn"}},
    {{"icon": "🌊", "text": "<strong>River</strong> — {rn}. the decisive river action"}}
  ],
  "explanation": "<strong>Answer: {lbl}</strong><br><br>2-3 sentences explaining why this hand fits the betting. Reference specific streets."
}}

Rules:
- CRITICAL: If the opponent did NOT improve on a street, do NOT write that they improved on that street
- CRITICAL: The explanation must correctly state that {lbl} makes {ctx['river_h']} — do not invent a different hand
- Use <strong> tags around card names and key actions
- Each event must be 1-2 punchy sentences
- The betting actions in events must match the template (calls, raises, checks) — only rewrite the prose style
- CRITICAL: The first event text MUST start exactly with "<strong>Your cards — {your_label}.</strong> " followed by the pre-flop action"""

    for attempt in range(3):
        resp = requests.post(
            'https://api.groq.com/openai/v1/chat/completions',
            headers={'Authorization': f'Bearer {GROQ_API_KEY}', 'Content-Type': 'application/json'},
            json={
                'model': GROQ_MODEL,
                'messages': [{'role': 'user', 'content': prompt}],
                'max_tokens': 700,
                'temperature': 0.3,
            },
            timeout=30,
        )
        resp.raise_for_status()
        content = resp.json()['choices'][0]['message']['content'].strip()
        cleaned = re.sub(r',\s*([}\]])', r'\1', extract_json(content))
        try:
            data = json.loads(cleaned)
            assert len(data['events']) == 4 and 'explanation' in data
            for e in data['events']:
                e['text'] = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', e['text'])
            return data['events'], data['explanation']
        except (json.JSONDecodeError, AssertionError, KeyError) as e:
            print(f'   LLM attempt {attempt+1} bad JSON ({e}), retrying...')

    raise ValueError('LLM failed to return valid JSON after 3 attempts')


# ─── Main ─────────────────────────────────────────────────
def generate_daily_hand():
    now   = datetime.datetime.now()
    today = now.date()
    base_seed = (now.year * 10000000000 + now.month * 100000000 + now.day * 1000000
                 + now.hour * 10000 + now.minute * 100 + now.second)

    your_cards = opp_cards = community = deck = None
    for attempt in range(30):
        random.seed(base_seed + attempt)
        deck = create_deck()
        random.shuffle(deck)
        your_cards = deal(deck, 2)
        opp_cards  = deal(deck, 2)
        community  = deal(deck, 5)
        if is_quality_hand(opp_cards, community):
            print(f'   Attempt {attempt+1}: {hand_name(opp_cards, community)} ✓')
            break
        print(f'   Attempt {attempt+1}: {hand_name(opp_cards, community)} — skipped')

    events, explanation, ctx = build_narrative(opp_cards, your_cards, community)

    used_llm = False
    try:
        events, explanation = llm_narrative(opp_cards, your_cards, community, events, explanation, ctx)
        used_llm = True
        print('   LLM narrative: ✓')
    except Exception as e:
        print(f'   LLM skipped ({e}), using template')

    wrong_raw = generate_wrong_choices(opp_cards, deck, community)

    correct_choice = {
        'cards': [[c['rank'], c['suit']] for c in opp_cards],
    }
    wrong_choices = [{
        'cards': [[c['rank'], c['suit']] for c in pair],
    } for pair, _ in wrong_raw]

    correct_idx = random.randint(0, 3)
    choices = wrong_choices[:correct_idx] + [correct_choice] + wrong_choices[correct_idx:]

    wrong_reasons = []
    wr_i = 0
    for i, choice in enumerate(choices):
        if i == correct_idx:
            wrong_reasons.append(None)
        else:
            pair_cards = [{'rank': c[0], 'suit': c[1]} for c in choice['cards']]
            wrong_reasons.append(build_wrong_reason(pair_cards, opp_cards, community, ctx))
            wr_i += 1

    hand = {
        'title':        'Daily Hand',
        'events':       events,
        'yourCards':    [[c['rank'], c['suit']] for c in your_cards],
        'community':    [[c['rank'], c['suit']] for c in community],
        'correct':      correct_idx,
        'choices':      choices,
        'wrongReasons': wrong_reasons,
        'explanation':  explanation,
    }

    with open('daily_hand.json', 'w') as f:
        json.dump({'date': now.isoformat(timespec='seconds'), 'hand': hand}, f, indent=2)

    print(f"   Date:      {now.isoformat(timespec='seconds')}")
    print(f"   You:       {fmt_card(your_cards[0])} {fmt_card(your_cards[1])}")
    print(f"   Opponent:  {fmt_card(opp_cards[0])} {fmt_card(opp_cards[1])} → {hand_name(opp_cards, community)}")
    print(f"   Board:     {' '.join(fmt_card(c) for c in community)}")
    print(f"   Flop:      {ctx['flop_h']}  Turn: {ctx['turn_h']}  River: {ctx['river_h']}")
    print(f"   Improved:  turn={ctx['improved_turn']}  river={ctx['improved_river']}")
    print(f"   Correct:   index {correct_idx} ({card_label(opp_cards)})")
    print(f"   Used LLM:  {used_llm}")


if __name__ == '__main__':
    generate_daily_hand()
