import itertools
import math
import time
from typing import Union

import jsonlines
import numpy as np
import trueskill
from prompt_toolkit import HTML
from prompt_toolkit import print_formatted_text as print
from prompt_toolkit import prompt
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.shortcuts import ProgressBar
from sklearn.model_selection import train_test_split

import openskill
from openskill.models import (
    BradleyTerryFull,
    BradleyTerryPart,
    PlackettLuce,
    ThurstoneMostellerFull,
    ThurstoneMostellerPart,
)

# Stores
os_players = {}
ts_players = {}

match_count = {}

matches = []
training_set = {}
test_set = {}
valid_test_set_matches = []

# Counters
os_correct_predictions = 0
os_incorrect_predictions = 0
ts_correct_predictions = 0
ts_incorrect_predictions = 0
confident_matches = 0


print(HTML("<u><b>Benchmark Starting</b></u>"))


def data_verified(match: dict) -> bool:
    result = match.get("result")
    if result not in ["WIN", "LOSS"]:
        return False

    teams: dict = match.get("teams")
    if list(teams.keys()) != ["blue", "red"]:
        return False

    blue_team: dict = teams.get("blue")
    red_team: dict = teams.get("red")

    if len(blue_team) < 1 and len(red_team) < 1:
        return False

    return True


def process_os_match(
    match: dict,
    model: Union[
        BradleyTerryFull,
        BradleyTerryPart,
        PlackettLuce,
        ThurstoneMostellerFull,
        ThurstoneMostellerPart,
    ] = PlackettLuce,
):
    result = match.get("result")
    won = True if result == "WIN" else False

    teams: dict = match.get("teams")
    blue_team: dict = teams.get("blue")
    red_team: dict = teams.get("red")

    os_blue_players = {}
    os_red_players = {}

    for player in blue_team:
        os_blue_players[player] = openskill.Rating()

    for player in red_team:
        os_red_players[player] = openskill.Rating()

    if won:
        blue_team_result, red_team_result = openskill.rate(
            [list(os_blue_players.values()), list(os_red_players.values())], model=model
        )
    else:
        red_team_result, blue_team_result = openskill.rate(
            [list(os_red_players.values()), list(os_blue_players.values())], model=model
        )

    os_blue_players = dict(zip(os_blue_players, blue_team_result))
    os_red_players = dict(zip(os_red_players, red_team_result))

    os_players.update(os_blue_players)
    os_players.update(os_red_players)


def process_ts_match(match: dict):
    result = match.get("result")
    won = True if result == "WIN" else False

    teams: dict = match.get("teams")
    blue_team: dict = teams.get("blue")
    red_team: dict = teams.get("red")

    ts_blue_players = {}
    ts_red_players = {}

    for player in blue_team:
        ts_blue_players[player] = trueskill.Rating()

    for player in red_team:
        ts_red_players[player] = trueskill.Rating()

    if won:
        blue_team_ratings, red_team_ratings = trueskill.rate(
            [list(ts_blue_players.values()), list(ts_red_players.values())],
        )
    else:
        red_team_ratings, blue_team_ratings = trueskill.rate(
            [list(ts_red_players.values()), list(ts_blue_players.values())]
        )

    ts_blue_players = dict(zip(ts_blue_players, blue_team_ratings))
    ts_red_players = dict(zip(ts_red_players, red_team_ratings))

    ts_players.update(ts_blue_players)
    ts_players.update(ts_red_players)


def predict_os_match(match: dict):
    result = match.get("result")
    won = True if result == "WIN" else False

    teams: dict = match.get("teams")
    blue_team: dict = teams.get("blue")
    red_team: dict = teams.get("red")

    os_blue_players = {}
    os_red_players = {}

    for player in blue_team:
        os_blue_players[player] = os_players[player]

    for player in red_team:
        os_red_players[player] = os_players[player]

    blue_win_probability, red_win_probability = openskill.predict_rank(
        [list(os_blue_players.values()), list(os_red_players.values())]
    )
    blue_win_probability = blue_win_probability[0]
    red_win_probability = red_win_probability[0]
    global os_correct_predictions
    if (blue_win_probability < red_win_probability) == won:
        os_correct_predictions += 1
    elif blue_win_probability == red_win_probability:  # Draw
        os_correct_predictions += 1
    else:
        global os_incorrect_predictions
        os_incorrect_predictions += 1


def win_probability(team1, team2):
    delta_mu = sum(r.mu for r in team1) - sum(r.mu for r in team2)
    sum_sigma = sum(r.sigma**2 for r in itertools.chain(team1, team2))
    size = len(team1) + len(team2)
    denom = math.sqrt(size * (trueskill.BETA * trueskill.BETA) + sum_sigma)
    ts = trueskill.global_env()
    return ts.cdf(delta_mu / denom)


def predict_ts_match(match: dict):
    result = match.get("result")
    won = True if result == "WIN" else False

    teams: dict = match.get("teams")
    blue_team: dict = teams.get("blue")
    red_team: dict = teams.get("red")

    ts_blue_players = {}
    ts_red_players = {}

    for player in blue_team:
        ts_blue_players[player] = ts_players[player]

    for player in red_team:
        ts_red_players[player] = ts_players[player]

    blue_win_probability = win_probability(
        list(ts_blue_players.values()), list(ts_red_players.values())
    )
    red_win_probability = abs(1 - blue_win_probability)
    if (blue_win_probability > red_win_probability) == won:
        global ts_correct_predictions
        ts_correct_predictions += 1
    else:
        global ts_incorrect_predictions
        ts_incorrect_predictions += 1


def process_match(match: dict):
    teams: dict = match.get("teams")
    blue_team: dict = teams.get("blue")
    red_team: dict = teams.get("red")

    for player in blue_team:
        match_count[player] = match_count.get(player, 0) + 1

    for player in red_team:
        match_count[player] = match_count.get(player, 0) + 1


def valid_test_set(match: dict):
    teams: dict = match.get("teams")
    blue_team: dict = teams.get("blue")
    red_team: dict = teams.get("red")

    for player in blue_team:
        if player not in os_players:
            return False

    for player in red_team:
        if player not in os_players:
            return False

    return True


def confident_in_match(match: dict) -> bool:
    teams: dict = match.get("teams")
    blue_team: dict = teams.get("blue")
    red_team: dict = teams.get("red")

    global confident_matches
    for player in blue_team:
        if match_count[player] < 2:
            return False

    for player in red_team:
        if match_count[player] < 2:
            return False

    confident_matches += 1
    return True


models = [
    BradleyTerryFull,
    BradleyTerryPart,
    PlackettLuce,
    ThurstoneMostellerFull,
    ThurstoneMostellerPart,
]
model_names = [m.__name__ for m in models]
model_completer = WordCompleter(model_names)
input_model = prompt("Enter Model: ", completer=model_completer)

if input_model in model_names:
    index = model_names.index(input_model)
else:
    print(HTML("<style fg='Red'>Model Not Found</style>"))
    quit()
with jsonlines.open("v2_jsonl_teams.jsonl") as reader:
    lines = list(reader.iter())

    title = HTML(f'<style fg="Red">Processing Matches</style>')
    with ProgressBar(title=title) as progress_bar:
        for line in progress_bar(lines, total=len(lines)):
            if data_verified(match=line):
                process_match(match=line)

    # Measure Confidence
    title = HTML(f'<style fg="Red">Splitting Data</style>')
    with ProgressBar(title=title) as progress_bar:
        for line in progress_bar(lines, total=len(lines)):
            if data_verified(match=line):
                if confident_in_match(match=line):
                    matches.append(line)

    # Split Data
    training_set, test_set = train_test_split(
        matches, test_size=0.33, random_state=True
    )

    # Process OpenSkill Ratings
    title = HTML(
        f'Updating Ratings with <style fg="Green">{input_model}</style> Model:'
    )
    with ProgressBar(title=title) as progress_bar:
        os_process_time_start = time.time()
        for line in progress_bar(training_set, total=len(training_set)):
            process_os_match(match=line, model=models[index])
    os_process_time_stop = time.time()
    os_time = os_process_time_stop - os_process_time_start

    # Process TrueSkill Ratings
    title = HTML(f'Updating Ratings with <style fg="Green">TrueSkill</style> Model:')
    with ProgressBar(title=title) as progress_bar:
        ts_process_time_start = time.time()
        for line in progress_bar(training_set, total=len(training_set)):
            process_ts_match(match=line)
    ts_process_time_stop = time.time()
    ts_time = ts_process_time_stop - ts_process_time_start

    # Process Test Set
    title = HTML(f'<style fg="Red">Processing Test Set</style>')
    with ProgressBar(title=title) as progress_bar:
        for line in progress_bar(test_set, total=len(test_set)):
            if valid_test_set(match=line):
                valid_test_set_matches.append(line)

    # Predict OpenSkill Matches
    title = HTML(f'<style fg="Blue">Predicting OpenSkill Matches:</style>')
    with ProgressBar(title=title) as progress_bar:
        for line in progress_bar(
            valid_test_set_matches, total=len(valid_test_set_matches)
        ):
            predict_os_match(match=line)

    # Predict TrueSkill Matches
    title = HTML(f'<style fg="Blue">Predicting TrueSkill Matches:</style>')
    with ProgressBar(title=title) as progress_bar:
        for line in progress_bar(
            valid_test_set_matches, total=len(valid_test_set_matches)
        ):
            predict_ts_match(match=line)

mean = float(np.array(list(match_count.values())).mean())

print(HTML(f"Confident Matches:  <style fg='Yellow'>{confident_matches}</style>"))
print(
    HTML(
        f"Predictions Made with OpenSkill's <style fg='Green'><u>{input_model}</u></style> Model:"
    )
)
print(
    HTML(
        f"Correct: <style fg='Yellow'>{os_correct_predictions}</style> | "
        f"Incorrect: <style fg='Yellow'>{os_incorrect_predictions}</style>"
    )
)
print(
    HTML(
        f"Accuracy: <style fg='Yellow'>"
        f"{round((os_correct_predictions/(os_incorrect_predictions + os_correct_predictions)) * 100, 2)}%"
        f"</style>"
    )
)
print(HTML(f"Process Duration: <style fg='Yellow'>{os_time}</style>"))
print("-" * 40)
print(HTML(f"Predictions Made with <style fg='Green'><u>TrueSkill</u></style> Model:"))
print(
    HTML(
        f"Correct: <style fg='Yellow'>{ts_correct_predictions}</style> | "
        f"Incorrect: <style fg='Yellow'>{ts_incorrect_predictions}</style>"
    )
)
print(
    HTML(
        f"Accuracy: <style fg='Yellow'>"
        f"{round((ts_correct_predictions/(ts_incorrect_predictions + ts_correct_predictions)) * 100, 2)}%"
        f"</style>"
    )
)
print(HTML(f"Process Duration: <style fg='Yellow'>{ts_time}</style>"))
print(HTML(f"Mean Matches: <style fg='Yellow'>{mean}</style>"))
