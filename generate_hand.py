"""
generate_hand.py

Generates a random poker hand for the daily Read the Hand challenge.
Uses today's date as the random seed so the hand is deterministic
(same hand for all users, no matter how many times this runs today).

Run manually:   python generate_hand.py
Run via CI:     GitHub Actions calls this on a daily cron schedule.

Output: daily_hand.json  (committed back to the repo by the workflow)
"""

import json
import random
import datetime

# ─── Card constants ──────────────────────────────────────
SUITS = ['s', 'h', 'd', 'c']
RANKS = ['2', '3', '4', '5', '6', '7', '8', '9', 'T', 'J', 'Q', 'K', 'A']
RANK_DISPLAY = {'T': '10', 'J': 'J', 'Q': 'Q', 'K': 'K', 'A': 'A'}
RANK_VAL = {r: i for i, r in enumerate(RANKS)}

def display(rank):
    return RANK_DISPLAY.get(rank, rank)

def card_str(card):
    return f"{display(card['rank'])}{card['suit']}"


# ─── Deck utilities ──────────────────────────────────────
def create_deck():
    return [{'rank': r, 'suit': s} for r in RANKS for s in SUITS]

def deal(deck, n):
    cards = deck[:n]
    del deck[:n]
    return cards


# ─── Hand evaluation (simplified) ────────────────────────
def hand_name(hole_cards, community):
    all_cards = hole_cards + community
    ranks = [RANK_VAL[c['rank']] for c in all_cards]
    suits = [c['suit'] for c in all_cards]

    rank_count = {}
    for r in ranks:
        rank_count[r] = rank_count.get(r, 0) + 1
    suit_count = {}
    for s in suits:
        suit_count[s] = suit_count.get(s, 0) + 1

    counts = sorted(rank_count.values(), reverse=True)
    has_flush = any(v >= 5 for v in suit_count.values())

    unique_ranks = sorted(set(ranks))
    has_straight = False
    for i in range(len(unique_ranks) - 4):
        if unique_ranks[i+4] - unique_ranks[i] == 4 and len(set(unique_ranks[i:i+5])) == 5:
            has_straight = True
            break
    # wheel (A-2-3-4-5)
    if not has_straight and 12 in unique_ranks and all(r in unique_ranks for r in [0,1,2,3]):
        has_straight = True

    if has_straight and has_flush: return 'Straight Flush'
    if counts[0] == 4:             return 'Four of a Kind'
    if counts[0] == 3 and len(counts) > 1 and counts[1] >= 2: return 'Full House'
    if has_flush:                  return 'Flush'
    if has_straight:               return 'Straight'
    if counts[0] == 3:             return 'Three of a Kind'
    if counts[0] == 2 and len(counts) > 1 and counts[1] == 2: return 'Two Pair'
    if counts[0] == 2:             return 'One Pair'
    return 'High Card'


# ─── Hole card strength (0–45 scale) ─────────────────────
def hole_strength(cards):
    a, b = sorted(cards, key=lambda c: RANK_VAL[c['rank']], reverse=True)
    paired   = a['rank'] == b['rank']
    suited   = a['suit'] == b['suit']
    connected = abs(RANK_VAL[a['rank']] - RANK_VAL[b['rank']]) <= 2
    score = RANK_VAL[a['rank']] * 2
    if paired:    score += 20
    if suited:    score += 5
    if connected: score += 3
    return score


# ─── Narrative generator ──────────────────────────────────
def fmt_card(c):
    suit_sym = {'s': '♠', 'h': '♥', 'd': '♦', 'c': '♣'}
    return f"{display(c['rank'])}{suit_sym[c['suit']]}"

def rank_name(r):
    names = {'2':'2','3':'3','4':'4','5':'5','6':'6','7':'7','8':'8','9':'9',
             'T':'10','J':'Jack','Q':'Queen','K':'King','A':'Ace'}
    return names.get(r, r)

def generate_narrative(player_cards, opp_cards, community, opp_strength):
    flop  = community[:3]
    turn  = community[3]
    river = community[4]

    strong  = opp_strength >= 32
    medium  = opp_strength >= 22

    flop_names  = ', '.join(rank_name(c['rank']) for c in flop)
    turn_name   = rank_name(turn['rank'])
    river_name  = rank_name(river['rank'])

    opp_hand = hand_name(opp_cards, community)

    if strong:
        events = [
            {'icon': '💰', 'text': 'Your opponent raised before the cards were dealt. You called.'},
            {'icon': '🃏', 'text': f'<strong>Flop</strong> — {flop_names}. Your opponent bet confidently. You called.'},
            {'icon': '🃏', 'text': f'<strong>Turn</strong> — {turn_name}. Your opponent bet again. <strong>You called.</strong>'},
            {'icon': '💥', 'text': f'<strong>River</strong> — {river_name}. Your opponent <strong>moved all-in.</strong>'},
        ]
    elif medium:
        events = [
            {'icon': '💰', 'text': 'You raised before the cards were dealt. Your opponent called.'},
            {'icon': '🃏', 'text': f'<strong>Flop</strong> — {flop_names}. You bet. <strong>Your opponent called.</strong>'},
            {'icon': '🃏', 'text': f'<strong>Turn</strong> — {turn_name}. You bet again. <strong>Your opponent called again.</strong>'},
            {'icon': '💥', 'text': f'<strong>River</strong> — {river_name}. Your opponent <strong>led out with a big bet.</strong>'},
        ]
    else:
        events = [
            {'icon': '💰', 'text': 'You raised before the cards were dealt. Your opponent called.'},
            {'icon': '🃏', 'text': f'<strong>Flop</strong> — {flop_names}. You bet. <strong>Your opponent called.</strong>'},
            {'icon': '🃏', 'text': f'<strong>Turn</strong> — {turn_name}. You checked. Your opponent checked.'},
            {'icon': '💥', 'text': f'<strong>River</strong> — {river_name}. Your opponent suddenly <strong>fired a large overbet.</strong>'},
        ]

    explanation = (
        f'<strong>Answer: {display(opp_cards[0]["rank"])} {display(opp_cards[1]["rank"])}</strong>'
        f'<br><br>Your opponent held {fmt_card(opp_cards[0])} {fmt_card(opp_cards[1])}, '
        f'making a <strong>{opp_hand}</strong> by the river. '
        f'Study the betting pattern — {"strong hands build the pot steadily" if strong else "medium hands call and look for a spot" if medium else "weak hands apply pressure suddenly on scary cards"}.'
    )

    return events, explanation


# ─── Wrong reason generator ───────────────────────────────
def generate_wrong_reason(wrong_choice, correct_choice, community):
    wr = wrong_choice['cards']
    cr = correct_choice['cards']
    wr_label = f"{display(wr[0][0])} {display(wr[1][0])}"
    reasons = [
        f"Players with {wr_label} wouldn't follow this exact betting line. The pattern fits a different type of hand — look at when the aggression started and what card triggered it.",
        f"{wr_label} doesn't explain the sizing and timing of the bets. Think about what hand would make someone bet this way on each specific street.",
        f"While {wr_label} is possible, the betting sequence doesn't match. Pay attention to when they were passive versus when they suddenly became aggressive.",
    ]
    return random.choice(reasons)


# ─── Choice generator ─────────────────────────────────────
def generate_choices(opp_cards, deck_remaining, community):
    """Generate 4 choices: the correct answer plus 3 plausible wrong ones."""
    correct = [opp_cards[0]['rank'], opp_cards[0]['suit'],
               opp_cards[1]['rank'], opp_cards[1]['suit']]

    # Pick 3 wrong pairs from remaining deck
    wrong_pairs = []
    available = [c for c in deck_remaining]
    random.shuffle(available)
    i = 0
    while len(wrong_pairs) < 3 and i + 1 < len(available):
        pair = [available[i], available[i+1]]
        # Avoid duplicating the correct answer ranks
        if not (pair[0]['rank'] == opp_cards[0]['rank'] and pair[1]['rank'] == opp_cards[1]['rank']):
            wrong_pairs.append(pair)
        i += 2

    def opp_hand_name(cards):
        return hand_name(cards, community)

    def make_choice(cards):
        r0, r1 = display(cards[0]['rank']), display(cards[1]['rank'])
        hand = opp_hand_name(cards)
        return {
            'cards': [[cards[0]['rank'], cards[0]['suit']], [cards[1]['rank'], cards[1]['suit']]],
            'label': f"{r0} {r1} — makes {hand}"
        }

    correct_choice = {
        'cards': [[opp_cards[0]['rank'], opp_cards[0]['suit']],
                  [opp_cards[1]['rank'], opp_cards[1]['suit']]],
        'label': f"{display(opp_cards[0]['rank'])} {display(opp_cards[1]['rank'])} — makes {opp_hand_name(opp_cards)}"
    }

    wrong_choices = [make_choice(p) for p in wrong_pairs]

    # Shuffle correct answer into a random position
    correct_idx = random.randint(0, 3)
    choices = wrong_choices[:correct_idx] + [correct_choice] + wrong_choices[correct_idx:]

    wrong_reasons = []
    for i, choice in enumerate(choices):
        if i == correct_idx:
            wrong_reasons.append(None)
        else:
            wrong_reasons.append(generate_wrong_reason(choice, correct_choice, community))

    return choices, correct_idx, wrong_reasons


# ─── Main ─────────────────────────────────────────────────
def generate_daily_hand():
    today = datetime.date.today()

    # Seed with today's date so it's deterministic across multiple runs
    seed = today.year * 10000 + today.month * 100 + today.day
    random.seed(seed)

    deck = create_deck()
    random.shuffle(deck)

    player_cards = deal(deck, 2)
    opp_cards    = deal(deck, 2)
    community    = deal(deck, 5)

    opp_strength = hole_strength(opp_cards)
    events, explanation = generate_narrative(player_cards, opp_cards, community, opp_strength)
    choices, correct_idx, wrong_reasons = generate_choices(opp_cards, deck, community)

    hand = {
        'title':        'Daily Hand',
        'events':       events,
        'yourCards':    [[c['rank'], c['suit']] for c in player_cards],
        'community':    [[c['rank'], c['suit']] for c in community],
        'correct':      correct_idx,
        'choices':      choices,
        'wrongReasons': wrong_reasons,
        'explanation':  explanation,
    }

    output = {
        'date': today.isoformat(),
        'hand': hand,
    }

    with open('daily_hand.json', 'w') as f:
        json.dump(output, f, indent=2)

    print(f"✅ Generated hand for {today}")
    print(f"   Your cards:  {fmt_card(player_cards[0])} {fmt_card(player_cards[1])}")
    print(f"   Opp cards:   {fmt_card(opp_cards[0])} {fmt_card(opp_cards[1])}")
    print(f"   Community:   {' '.join(fmt_card(c) for c in community)}")
    print(f"   Opp hand:    {hand_name(opp_cards, community)}")
    print(f"   Correct idx: {correct_idx}")


if __name__ == '__main__':
    generate_daily_hand()
