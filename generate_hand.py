"""
generate_hand.py

Generates a daily poker hand-reading puzzle for "Read the Hand".

Quality improvements over v1:
- Retries the deal until the opponent has at least One Pair (interesting hands only)
- Narrative templates are specific to the hand type (set, two pair, flush, bluff, etc.)
- Wrong choices are strategically chosen: one stronger hand, one similar, one weaker
- Wrong reasons are tied to the specific betting pattern in the story
- Optionally uses a HuggingFace LLM (set HF_TOKEN) for even richer narratives

Run:   python generate_hand.py
Output: daily_hand.json
"""

import json, os, random, datetime, requests

# ─── Config ──────────────────────────────────────────────
GROQ_API_KEY = os.environ.get('GROQ_API_KEY', '')
GROQ_MODEL   = 'llama-3.3-70b-versatile'  # free on Groq, much better quality than 7B models

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


# ─── Quality filter ───────────────────────────────────────
def is_quality_hand(opp_cards, community):
    """Only accept hands with at least One Pair — ensures a clear narrative."""
    h = hand_name(opp_cards, community)
    return HAND_RANK[h] >= HAND_RANK['One Pair']


# ─── Narrative templates (hand-type specific) ─────────────
def build_narrative(opp_cards, community):
    flop  = community[:3]
    turn  = community[3]
    river = community[4]
    fn = ', '.join(rank_name(c['rank']) for c in flop)
    tn = rank_name(turn['rank'])
    rn = rank_name(river['rank'])

    h  = hand_name(opp_cards, community)
    hr = HAND_RANK[h]
    lbl = card_label(opp_cards)

    opp_rv = [RANK_VAL[c['rank']] for c in opp_cards]
    board_rv = [RANK_VAL[c['rank']] for c in community]
    flop_rv  = [RANK_VAL[c['rank']] for c in flop]

    # Did either hole card pair with the flop?
    pairs_flop = any(rv in flop_rv for rv in opp_rv)
    # Pocket pair?
    pocket_pair = opp_rv[0] == opp_rv[1]
    # Pocket pair above all flop cards?
    overpair = pocket_pair and opp_rv[0] > max(flop_rv)

    if hr >= HAND_RANK['Full House']:
        # Monster — slow play then trap
        events = [
            {'icon':'💰','text':'You raised before the cards were dealt. Your opponent called.'},
            {'icon':'🃏','text':f'<strong>Flop</strong> — {fn}. You bet. <strong>Your opponent just called.</strong>'},
            {'icon':'🃏','text':f'<strong>Turn</strong> — {tn}. You bet again. <strong>Your opponent raised you.</strong>'},
            {'icon':'💥','text':f'<strong>River</strong> — {rn}. Your opponent <strong>moved all-in.</strong>'},
        ]
        explanation = (
            f'<strong>Answer: {lbl}</strong><br><br>'
            f'Your opponent flopped a {h.lower()} and slow-played the flop, calling to disguise their strength. '
            f'Once the pot was big enough they raised the turn and shoved the river — a classic trap with a monster hand.'
        )
        wrong_hint = 'trap'

    elif hr == HAND_RANK['Flush']:
        events = [
            {'icon':'💰','text':'Your opponent called your raise before the cards were dealt.'},
            {'icon':'🃏','text':f'<strong>Flop</strong> — {fn}. You bet. <strong>Your opponent called.</strong>'},
            {'icon':'🃏','text':f'<strong>Turn</strong> — {tn}. You checked. <strong>Your opponent bet.</strong>'},
            {'icon':'💥','text':f'<strong>River</strong> — {rn}. Your opponent <strong>bet large — about the size of the pot.</strong>'},
        ]
        explanation = (
            f'<strong>Answer: {lbl}</strong><br><br>'
            f'Your opponent was drawing to a flush and called the flop to keep their options open. '
            f'Once they hit, they bet the turn and river for value — growing aggression on later streets is a hallmark of a completed draw.'
        )
        wrong_hint = 'draw'

    elif hr == HAND_RANK['Straight']:
        events = [
            {'icon':'💰','text':'Both players called a raise before the cards were dealt.'},
            {'icon':'🃏','text':f'<strong>Flop</strong> — {fn}. You bet. <strong>Your opponent raised.</strong>'},
            {'icon':'🃏','text':f'<strong>Turn</strong> — {tn}. Your opponent led out with a bet. <strong>You called.</strong>'},
            {'icon':'💥','text':f'<strong>River</strong> — {rn}. Your opponent <strong>fired again — half the pot.</strong>'},
        ]
        explanation = (
            f'<strong>Answer: {lbl}</strong><br><br>'
            f'Your opponent flopped a straight and immediately raised to build the pot. '
            f'They led every remaining street — consistent value betting across all streets is a strong signal of a made hand.'
        )
        wrong_hint = 'value'

    elif hr == HAND_RANK['Three of a Kind']:
        events = [
            {'icon':'💰','text':'You raised before the cards were dealt. Your opponent called.'},
            {'icon':'🃏','text':f'<strong>Flop</strong> — {fn}. You bet. <strong>Your opponent raised you back.</strong>'},
            {'icon':'🃏','text':f'<strong>Turn</strong> — {tn}. Your opponent bet out. <strong>You called.</strong>'},
            {'icon':'💥','text':f'<strong>River</strong> — {rn}. Your opponent <strong>shoved all-in without hesitation.</strong>'},
        ]
        explanation = (
            f'<strong>Answer: {lbl}</strong><br><br>'
            f'Your opponent flopped three of a kind and raised the flop immediately to build the pot. '
            f'Sets are too strong to slow-play — they pushed their advantage on every street.'
        )
        wrong_hint = 'aggression'

    elif hr == HAND_RANK['Two Pair']:
        events = [
            {'icon':'💰','text':'Your opponent raised before the cards were dealt. You called.'},
            {'icon':'🃏','text':f'<strong>Flop</strong> — {fn}. Your opponent bet. <strong>You called.</strong>'},
            {'icon':'🃏','text':f'<strong>Turn</strong> — {tn}. <strong>Your opponent bet again — bigger.</strong>'},
            {'icon':'💥','text':f'<strong>River</strong> — {rn}. Your opponent <strong>fired a large river bet.</strong>'},
        ]
        explanation = (
            f'<strong>Answer: {lbl}</strong><br><br>'
            f'Your opponent flopped two pair and bet all three streets with growing sizing. '
            f'The consistent pressure — especially the larger turn bet — signals a hand strong enough to protect but not quite a monster.'
        )
        wrong_hint = 'value'

    elif hr == HAND_RANK['One Pair'] and overpair:
        events = [
            {'icon':'💰','text':'Your opponent raised before the cards were dealt. You called.'},
            {'icon':'🃏','text':f'<strong>Flop</strong> — {fn}. Your opponent bet. <strong>You called.</strong>'},
            {'icon':'🃏','text':f'<strong>Turn</strong> — {tn}. Your opponent bet again. <strong>You called.</strong>'},
            {'icon':'💥','text':f'<strong>River</strong> — {rn}. <strong>Your opponent checked — then called your bet.</strong>'},
        ]
        explanation = (
            f'<strong>Answer: {lbl}</strong><br><br>'
            f'Your opponent had an overpair and bet the flop and turn for value but checked back the river — '
            f'a sign they were confident earlier but wanted pot control once the board got scarier.'
        )
        wrong_hint = 'overpair'

    elif hr == HAND_RANK['One Pair'] and pairs_flop:
        events = [
            {'icon':'💰','text':'You raised before the cards were dealt. Your opponent called.'},
            {'icon':'🃏','text':f'<strong>Flop</strong> — {fn}. Your opponent checked. You bet. <strong>Your opponent called.</strong>'},
            {'icon':'🃏','text':f'<strong>Turn</strong> — {tn}. Both players checked.'},
            {'icon':'💥','text':f'<strong>River</strong> — {rn}. Your opponent <strong>led out with a bet.</strong>'},
        ]
        explanation = (
            f'<strong>Answer: {lbl}</strong><br><br>'
            f'Your opponent flopped a pair but played it passively — calling the flop and checking the turn — '
            f'before leading the river to extract thin value. The check-call-check-lead line is a tell for a medium-strength made hand.'
        )
        wrong_hint = 'passive'

    else:
        # Generic one pair — continuation bet style
        events = [
            {'icon':'💰','text':'You raised before the cards were dealt. Your opponent called.'},
            {'icon':'🃏','text':f'<strong>Flop</strong> — {fn}. You bet. <strong>Your opponent called.</strong>'},
            {'icon':'🃏','text':f'<strong>Turn</strong> — {tn}. You bet. <strong>Your opponent raised.</strong>'},
            {'icon':'💥','text':f'<strong>River</strong> — {rn}. Your opponent <strong>bet again when checked to.</strong>'},
        ]
        explanation = (
            f'<strong>Answer: {lbl}</strong><br><br>'
            f'Your opponent called the flop and then raised the turn — a line that often means they just hit something. '
            f'The river bet confirmed they had a real hand and were going for value.'
        )
        wrong_hint = 'value'

    return events, explanation, wrong_hint


# ─── Strategic wrong choices ─────────────────────────────
def generate_wrong_choices(opp_cards, deck_remaining, community, wrong_hint):
    """
    Return 3 wrong card pairs that are strategically interesting:
    - one that would make a STRONGER hand (would usually play bigger)
    - one that's SIMILAR strength (hardest to eliminate)
    - one that's WEAKER (wouldn't justify this betting line)
    """
    correct_h  = hand_name(opp_cards, community)
    correct_hr = HAND_RANK[correct_h]

    available = list(deck_remaining)
    random.shuffle(available)

    stronger, similar, weaker = [], [], []
    for i in range(0, len(available) - 1, 2):
        pair = [available[i], available[i+1]]
        h  = hand_name(pair, community)
        hr = HAND_RANK[h]
        if hr > correct_hr:   stronger.append((pair, h))
        elif hr == correct_hr: similar.append((pair, h))
        else:                  weaker.append((pair, h))

    chosen = []
    # One similar (trickiest decoy), one stronger, one weaker
    for pool in [similar, stronger, weaker]:
        if pool and len(chosen) < 3:
            chosen.append(pool[0])

    # Fill any remaining slots
    all_pairs = [(available[i:i+2], hand_name(available[i:i+2], community))
                 for i in range(0, len(available)-1, 2)]
    for pair, h in all_pairs:
        if len(chosen) >= 3: break
        if not any(pair == c[0] for c in chosen):
            chosen.append((pair, h))

    return chosen[:3]


# ─── Wrong reasons tied to the betting pattern ───────────
def build_wrong_reason(wrong_pair, correct_pair, community, wrong_hint):
    wl  = card_label(wrong_pair)
    cl  = card_label(correct_pair)
    wh  = hand_name(wrong_pair, community)
    ch  = hand_name(correct_pair, community)
    whr = HAND_RANK[wh]
    chr = HAND_RANK[ch]

    if whr > chr:
        # Wrong choice is stronger than correct
        templates = [
            f"{wl} makes a {wh.lower()} here — that's too strong a hand to play this way. They would have raised earlier and sized up to extract maximum value.",
            f"With a {wh.lower()}, {wl} would likely play faster and bigger. The measured bet sizes in this hand suggest something more modest.",
            f"{wl} gives you a {wh.lower()} — a hand most players protect aggressively. The passive moments in this hand don't fit.",
        ]
    elif whr < chr:
        # Wrong choice is weaker
        templates = [
            f"{wl} has very little equity on this board — a player with {wh.lower()} wouldn't follow through with this betting line across multiple streets.",
            f"With only {wh.lower()}, {wl} can't comfortably call a raise and then continue betting. The aggression here requires a real hand.",
            f"{wl} misses too much of this board to justify the multi-street commitment shown. Look at which hands benefit from building the pot this way.",
        ]
    else:
        # Similar strength — subtler reason
        templates = [
            f"{wl} is a reasonable guess, but look at the specific street where the aggression shifted — that card helps the correct hand more than {wl}.",
            f"While {wl} has similar strength, the bet sizing and timing don't quite fit. Think about what specific board cards benefit the correct hand.",
            f"{wl} could play a similar line, but the check on the turn is the tell — that card changes things for the correct hand in a way {wl} doesn't benefit from.",
        ]

    return random.choice(templates)


# ─── LLM upgrade (optional) ──────────────────────────────
def llm_narrative(opp_cards, your_cards, community, template_events, template_explanation):
    """Enhance the template narrative with Groq's Llama 3.3 70B. Falls back to template on failure."""
    if not GROQ_API_KEY:
        raise ValueError('No GROQ_API_KEY')

    flop, turn, river = community[:3], community[3], community[4]
    fn = ', '.join(rank_name(c['rank']) for c in flop)
    tn, rn = rank_name(turn['rank']), rank_name(river['rank'])
    lbl   = card_label(opp_cards)
    opp_h = hand_name(opp_cards, community)

    scaffold = '\n'.join(f'- {e["text"]}' for e in template_events)

    prompt = f"""You write content for a daily poker puzzle game called "Read the Hand".
Players see a play-by-play and must guess what hole cards the opponent held from 4 options.

HAND FACTS:
- Opponent holds: {lbl} (makes {opp_h} by the river)
- Flop: {fn} | Turn: {tn} | River: {rn}

TEMPLATE NARRATIVE TO REWRITE (make it vivid, specific, and feel like a real hand with money on the line):
{scaffold}

TEMPLATE EXPLANATION TO REWRITE:
{template_explanation}

Return ONLY this JSON — no markdown, no extra text:
{{
  "events": [
    {{"icon": "💰", "text": "pre-flop action in 1-2 sentences"}},
    {{"icon": "🃏", "text": "<strong>Flop</strong> — {fn}. what happened on the flop"}},
    {{"icon": "🃏", "text": "<strong>Turn</strong> — {tn}. what happened on the turn"}},
    {{"icon": "💥", "text": "<strong>River</strong> — {rn}. the decisive river action"}}
  ],
  "explanation": "<strong>Answer: {lbl}</strong><br><br>2-3 sentences explaining why this hand makes sense given the betting shown. Reference specific streets."
}}

Rules:
- Use <strong> tags around card names and key actions
- Card names must exactly match: {fn}, {tn}, {rn}
- Keep each event to 1-2 punchy sentences
- The betting pattern must logically fit {lbl} making {opp_h}"""

    resp = requests.post(
        'https://api.groq.com/openai/v1/chat/completions',
        headers={'Authorization': f'Bearer {GROQ_API_KEY}', 'Content-Type': 'application/json'},
        json={
            'model': GROQ_MODEL,
            'messages': [{'role': 'user', 'content': prompt}],
            'max_tokens': 700,
            'temperature': 0.75,
        },
        timeout=30,
    )
    resp.raise_for_status()
    content = resp.json()['choices'][0]['message']['content'].strip()
    start, end = content.find('{'), content.rfind('}') + 1
    data = json.loads(content[start:end])
    assert len(data['events']) == 4 and 'explanation' in data
    return data['events'], data['explanation']


# ─── Main ─────────────────────────────────────────────────
def generate_daily_hand():
    today = datetime.date.today()
    base_seed = today.year * 10000 + today.month * 100 + today.day

    # Retry until we deal an interesting hand (opp has at least One Pair)
    your_cards = opp_cards = community = deck = None
    for attempt in range(20):
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

    # Build narrative from hand-type-specific template
    events, explanation, wrong_hint = build_narrative(opp_cards, community)

    # Try LLM enhancement
    used_llm = False
    try:
        events, explanation = llm_narrative(opp_cards, your_cards, community, events, explanation)
        used_llm = True
        print('   LLM narrative: ✓')
    except Exception as e:
        print(f'   LLM skipped ({e}), using template')

    # Strategic wrong choices
    wrong_raw = generate_wrong_choices(opp_cards, deck, community, wrong_hint)

    # Assemble choices
    correct_choice = {
        'cards': [[c['rank'], c['suit']] for c in opp_cards],
        'label': card_label(opp_cards),
    }
    wrong_choices = [{
        'cards': [[c['rank'], c['suit']] for c in pair],
        'label': card_label(pair),
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
            wrong_reasons.append(build_wrong_reason(pair_cards, opp_cards, community, wrong_hint))
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
        json.dump({'date': today.isoformat(), 'hand': hand}, f, indent=2)

    print(f"   Date:      {today}")
    print(f"   You:       {fmt_card(your_cards[0])} {fmt_card(your_cards[1])}")
    print(f"   Opponent:  {fmt_card(opp_cards[0])} {fmt_card(opp_cards[1])} → {hand_name(opp_cards, community)}")
    print(f"   Board:     {' '.join(fmt_card(c) for c in community)}")
    print(f"   Correct:   index {correct_idx} ({card_label(opp_cards)})")
    print(f"   Used LLM:  {used_llm}")


if __name__ == '__main__':
    generate_daily_hand()
