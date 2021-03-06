from __future__ import division
import time
import copy
import csv
import math
import ast
import pandas as pd
import numpy as np
from numpy import dot
from numpy.linalg import norm
from statistics import mean
from os import path
from ModelGlobal import global_model
from Ranking import ndcg_at, mapk, precision_at

# Re-ranks + evaluates a given initial recommendation list, its users, songs, interactions and contextual dimensions
# Re-rank algorithm uses personal mapping in this class
# Only need to re-rank the biggest recommendation list for each algorithm, SubReRankPersonal will do smaller sizes
np.set_printoptions(precision=2)
start_time = time.time()

# GLOBAL PARAMETERS
d_set = 'nprs'  # nprs or car (not supported at the moment)
algoName = "RankALS" # Any initial recommendation algorithm that you wish to re-rank (BPR, US-BPR, RankALS in our case)
test_item_amount = 200  # Keep at 200, SubReRankGlobal takes care of 25, 50 and 100
k = 5

dist_metric = 'euclidean' # euclidean or cosine
metric_to_use = "MAP" # MAP or Prec
metrics_sizes = [10, 25, 'all']
dimension = 'daytime'

# Only daytime is relevant for the #NowPlaying-RS dataset
if dimension == 'weather':
    conditions = ['rainy', 'cloudy', 'snowing', 'sunny']
elif dimension == 'mood':
    conditions = ['active', 'sad', 'lazy']
elif dimension == 'driving-style':
    conditions = ['relaxed driving', 'sport driving']
elif dimension == 'roadtype':
    conditions = ['city', 'highway', 'serpentine']
elif dimension == 'daytime':
    conditions = ['morning', 'afternoon', 'evening', 'night']

metrics_positive_only = False
shift_scores = False
multiple_contexts = False

input_rec_path = f'input\\{d_set}_{test_item_amount}\\'
user_context_ratings = pd.read_csv(f'input\\{d_set}\\user_context_sums.csv', delimiter=',', keep_default_na=False)
songs = pd.read_csv(f'input\\{d_set}\\{d_set}_audio_features.csv', delimiter=',')
AUDIO_FEATURES = ["acousticness", "danceability", "energy", "instrumentalness", "loudness",
                  "speechiness", "tempo", "valence"]
if 'nprs' in input_rec_path:
    # 3 = acousticness, 4 = danceability, 5 = energy, 6 = instrumentalness, 7 = key, 8 = liveness, 9 = loudness,
    # 10 = speechiness, 11 = tempo, 12 = valence
    invalid_songs = [38, 5837]
else:
    invalid_songs = [758]

pos_rating_weights = [1, 1, 1]  # For ratings 3, 4 and 5

# Variables to hold information that's needed across different folds
global_models = {}
personal_models = {}
folds = []
fold_item_init_dist = {}
initial_pred_list = {}

# Variables used to write the resulting output file
if metric_to_use == 'Prec':
    final = {'lambda': [], f'{metric_to_use}_{metrics_sizes[0]}_initial': [],
             f'{metric_to_use}_{metrics_sizes[0]}_rerank': [],
             f'{metric_to_use}_{metrics_sizes[1]}_initial': [], f'{metric_to_use}_{metrics_sizes[1]}_rerank': []}
else:
    final = {'lambda': [], f'{metric_to_use}_{metrics_sizes[0]}_initial': [],
             f'{metric_to_use}_{metrics_sizes[0]}_rerank': [],
             f'{metric_to_use}_{metrics_sizes[1]}_initial': [], f'{metric_to_use}_{metrics_sizes[1]}_rerank': [],
             f'{metric_to_use}_{metrics_sizes[2]}_initial': [], f'{metric_to_use}_{metrics_sizes[2]}_rerank': []}

ind = 1
while ind <= k:
    folds.append(f'{algoName}-{ind}')
    ind = ind + 1
lambdas = [0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1]

# Keeps track of all calculated distances and used initial ratings for normalization later
all_distances = []
all_initial_ratings = []

all_old = []
all_new = {0: [], 0.1: [], 0.2: [], 0.3: [], 0.4: [], 0.5: [], 0.6: [], 0.7: [], 0.8: [], 0.9: [], 1: []}
all_correct = {}


# Read initial recommendation as input
def read_java_output(file):
    def tple(inp):
        if type(inp) is str:
            resu = tuple(inp.split(';'))
            all_initial_ratings.append(float(resu[1]))
            return int(resu[0]), float(resu[1])
        return 0, 0

    def crt_items(items):
        if type(items) is int:
            return [items]
        else:
            return list(map(int, items.split(';')))

    def context_map(ctx):
        return list(ctx.split(';'))

    def extract_rec_amount(file):
        with open(f'{input_rec_path}{file}.csv', newline='') as f:
            reader = csv.reader(f)
            amount = next(reader)  # gets the first line
            return int(amount[0])

    initial_pred = pd.read_csv(f'{input_rec_path}{file}.csv', delimiter=',', skiprows=1)
    cols = ['userId', 'correctItems', 'contexts']
    recommendations = []
    for i in range(1, extract_rec_amount(file) + 1):
        recommendations.append(f'p{i}')
    cols = cols + recommendations
    initial_pred['correctItems'] = initial_pred['correctItems'].apply(lambda x: crt_items(x))
    initial_pred['contexts'] = initial_pred['contexts'].apply(lambda x: context_map(x))
    for col in initial_pred[recommendations]:
        initial_pred[col] = initial_pred[col].apply(lambda x: tple(x))

    res = []
    for index, row in initial_pred.iterrows():
        r = []
        for c in cols:
            if c == 'contexts':
                for cxt in row[c]:
                    r.append(cxt)
            else:
                r.append(row[c])
        res.append(r)

    return res


def personal_model(fold):
    contexts = ["night", "evening", "afternoon", "morning"]

    if path.isfile(f'{input_rec_path}{f}-personal-model.csv'):
        model = pd.read_csv(f'{input_rec_path}{f}-personal-model.csv', delimiter=',')
        model_final = {}
        for user in model.columns:
            if int(user) not in model_final:
                model_final[int(user)] = {}
            for idx, cntx in enumerate(contexts):
                model_final[int(user)][cntx] = ast.literal_eval(model[user][idx])
        personal_models[fold] = model_final
    else:
        num = [int(s) for s in fold.split('-') if s.isdigit()]
        test_set = pd.read_csv(f'input\\{d_set}_test\\{num[0]}-test.csv', delimiter=',')

        for index, row in test_set.iterrows():
            if d_set == 'car' and row['Rating'] >= 3:
                if row['Item'] not in invalid_songs:
                    song_afs = songs.loc[songs['id'] == row['Item']]
                    for af in AUDIO_FEATURES:
                        personal_models[fold][row['User']][row['Context'].split(':')[1]][af] = \
                            personal_models[fold][row['User']][row['Context'].split(':')[1]][af] - song_afs[af].item()
                    personal_models[fold][row['User']][row['Context'].split(':')[1]]['Amount'] = \
                        personal_models[fold][row['User']][row['Context'].split(':')[1]]['Amount'] - 1

        for user in personal_models[fold]:
            for cntx in personal_models[fold][user]:
                for af in AUDIO_FEATURES:
                    if personal_models[fold][user][cntx]['Amount'] > 0:
                        personal_models[fold][user][cntx][af] = personal_models[fold][user][cntx][af] / \
                                                                personal_models[fold][user][cntx]['Amount']

        pl_df = pd.DataFrame.from_dict(personal_models[fold])
        pl_df.to_csv(f'{input_rec_path}{f}-personal-model.csv', index=False, float_format='%.5f')
    print(f'Personal model built for {fold} in {time.time() - start_time} s')

# Calculates the distance based on audio features
def calculate_distance(contexts, item_prediction, fold, user_id):
    def avg_afs_multiple_songs(song_rating, item_id):
        all_feat = {"acousticness": [], "danceability": [], "energy": [], "instrumentalness": [], "loudness": [],
                    "speechiness": [], "tempo": [], "valence": []}
        feat_avg = {}
        sum_used_ratings = 0
        unused_items = 0

        for (song_id, rating) in song_rating:
            if song_id not in invalid_songs and song_id != item_id:
                row = songs.loc[songs['id'] == song_id]
                weight = pos_rating_weights[0]
                if rating == 4:
                    weight = pos_rating_weights[1]
                elif rating == 5:
                    weight = pos_rating_weights[2]
                sum_used_ratings = sum_used_ratings + weight
                for af in AUDIO_FEATURES:
                    all_feat[af].append(weight * row[af].item())
            else:
                unused_items = unused_items + 1

        for af in all_feat:
            feat_average = sum(all_feat[af]) / len(all_feat[af]) / (sum_used_ratings / (len(songs) - unused_items))
            feat_avg[af] = feat_average

        return feat_avg

    # Get saved audio features for a given song
    def get_song_afs(song_id):
        afs = []
        row = songs.loc[songs['id'] == song_id]
        for af in AUDIO_FEATURES:
            afs.append(row[af].item())
        return afs

    def personal_dist(contexts, song_afs, fold, user_id, item_id):
        distances = []
        if dimension != '':
            for c in contexts:
                if c not in conditions:
                    return -99
        # if 'nprs_initial' in input_rec_path:
        for c in contexts:
            if dimension == '':
                afs = []
                if personal_models[fold][user_id][c]['Amount'] <= 0:
                    for af in AUDIO_FEATURES:
                        afs.append(global_models[fold][c][af])
                else:
                    for af in AUDIO_FEATURES:
                        afs.append(personal_models[fold][user_id][c][af])
                if 0 not in afs:
                    if dist_metric == 'euclidean':
                        distances.append(np.linalg.norm(song_afs - afs))
                    elif dist_metric == 'cosine':
                        distances.append(dot(song_afs, afs) / (norm(song_afs) * norm(afs)))
                else:
                    afs = []
                    for af in AUDIO_FEATURES:
                        afs.append(global_models[fold][c][af])
                    if dist_metric == 'euclidean':
                        distances.append(np.linalg.norm(song_afs - afs))
                    elif dist_metric == 'cosine':
                        distances.append(dot(song_afs, afs) / (norm(song_afs) * norm(afs)))
            else:
                if c in conditions:
                    afs = []
                    if c not in personal_models[fold][user_id]:
                        for af in AUDIO_FEATURES:
                            afs.append(global_models[fold][c][af])
                    else:
                        if personal_models[fold][user_id][c]['Amount'] <= 0:
                            for af in AUDIO_FEATURES:
                                afs.append(global_models[fold][c][af])
                        else:
                            for af in AUDIO_FEATURES:
                                afs.append(personal_models[fold][user_id][c][af])
                        if 0 not in afs:
                            if dist_metric == 'euclidean':
                                distances.append(np.linalg.norm(song_afs - afs))
                            elif dist_metric == 'cosine':
                                distances.append(dot(song_afs, afs) / (norm(song_afs) * norm(afs)))
                        else:
                            afs = []
                            for af in AUDIO_FEATURES:
                                afs.append(global_models[fold][c][af])
                            if dist_metric == 'euclidean':
                                distances.append(np.linalg.norm(song_afs - afs))
                            elif dist_metric == 'cosine':
                                distances.append(dot(song_afs, afs) / (norm(song_afs) * norm(afs)))
        if len(distances) > 0 :
            return mean(distances)
        return -99

    item = item_prediction[0]
    initial_pred = item_prediction[1]

    if item == 0:
        return 0, 0, -99
    elif item in invalid_songs:
        return item, initial_pred, -99
    else:
        song_afs = np.array(get_song_afs(item))
        dist = personal_dist(contexts, song_afs, fold, user_id, item)
        if isinstance(dist, int) or isinstance(dist, float) and not math.isnan(dist):
            if dist != -99:
                all_distances.append(dist)
            return item, initial_pred, dist
        else:
            return item, initial_pred, -99


# Re-ranks a given list of initial recommendations using a specific lambda
def re_rank(initial_predictions, new_preds):
    # Calculates the final score on which the re-ranking is based using a mix of both the initial ranking and the
    # calculated distance based on audio features
    def calculate_scores(item_init_dist):
        new_scores = {0: [], 0.1: [], 0.2: [], 0.3: [], 0.4: [], 0.5: [], 0.6: [], 0.7: [], 0.8: [], 0.9: [], 1: []}
        init_denom = max_init - min_init
        if init_denom == 0:
            init_denom = 1
        dist_denom = max_dist - min_dist
        if dist_denom == 0:
            dist_denom = 1
        for (item, initial_pred, distance) in item_init_dist:
            if distance != -99:
                norm_init = (initial_pred - min_init) / init_denom
                if dist_metric == 'euclidean':
                    norm_dist = (1 - (distance - min_dist) / dist_denom)
                elif dist_metric == 'cosine':
                    norm_dist = (distance - min_dist) / dist_denom
                for l in lambdas:
                    new_scores[l].append((item, ((1 - l) * norm_init + l * norm_dist)))
                    # print(f'init {div_factor_all_ratings} | dist {div_factor_distances} | new_score {new_scores}')
            else:
                for l in lambdas:
                    new_scores[l].append((item,-99))
        return new_scores

    def get_rating_of_tuple(t):
        return t[1]

    new_preds = calculate_scores(new_preds)
    if new_preds[0] != -99:
        for l in lambdas:
            new_preds[l] = sorted(new_preds[l], key=get_rating_of_tuple, reverse=True)

        old = []
        new = {0: [], 0.1: [], 0.2: [], 0.3: [], 0.4: [], 0.5: [], 0.6: [], 0.7: [], 0.8: [], 0.9: [], 1: []}

        for t in initial_predictions:
            old.append(t[0])
        for l in lambdas:
            for t in new_preds[l]:
                new[l].append(t[0])

        all_old.append(old)
        for l in lambdas:
            all_new[l].append(new[l])


# Keeping track of all correct items for each fold and user-item case
def save_correct_items(fold, recommendation):
    for i, c in enumerate(recommendation):
        if isinstance(c, list):
            all_correct[fold].append(c)
            break


# Extracts the specific contexts in which the initial recommendation was made
def extract_contexts(recommendation):
    contexts = []
    for i, c in enumerate(recommendation[2:]):
        if type(c) is str:
            contexts.append(c.split(":", 1)[1])
        else:
            break
    return contexts


# Extracts the prediction part of the whole initial recommendation
def extract_predictions(recommendation):
    for i, c in enumerate(recommendation):
        if type(c) is tuple:
            return recommendation[i:]
    return []


# Extract distance tuples of (item, initial prediction, distance)
def parse_distance(inp):
    if type(inp) is str:
        inp = inp.replace("(", "")
        inp = inp.replace(")", "")
        resu = tuple(inp.split(','))
        return int(resu[0]), float(resu[1]), float(resu[2])
    return 0, 0, 0


user_sums = {}
for i, row in user_context_ratings.iterrows():
    if row['User'] not in user_sums:
        user_sums[row['User']] = {}
    user_sums[row['User']][row['Context']] = {}
    for af in AUDIO_FEATURES:
        user_sums[row['User']][row['Context']][af] = row[af]
    user_sums[row['User']][row['Context']]['Amount'] = row['Amount']

# Creating global models based on global audio feature averages of certain contexts as back up if not enough personal
for f in folds:
    num = [int(s) for s in f.split('-') if s.isdigit()]
    if path.isfile(f'input\\{d_set}_global_model\\{num[0]}-global-model.csv'):
        model = pd.read_csv(f'input\\{d_set}_global_model\\{num[0]}-global-model.csv', delimiter=',')
        model_final = {}
        for cntx in model:
            model_final[cntx] = {}
            for num, af in enumerate(AUDIO_FEATURES):
                model_final[cntx][af] = model[cntx][num]
        global_models[f] = model_final
    else:
        test_items = []
        initial_pred_list[f] = read_java_output(f)
        for r in initial_pred_list[f]:
            test_items = test_items + r[1]
        global_models[f] = global_model(f, d_set, songs, copy.deepcopy(user_sums), num[0], invalid_songs)

        gb_df = pd.DataFrame.from_dict(global_models[f])
        gb_df.to_csv(f'{input_rec_path}{f}-global-model.csv', index=False, float_format='%.5f')
    print(f'Global model built for {f}, in {time.time() - start_time} s')

# Go through each fold
for f in folds:
    all_correct[f] = []
    if path.isfile(f'{input_rec_path}{algoName}-personal-general.csv'):
        distances = pd.read_csv(f'{input_rec_path}{f}-personal-distance.csv', delimiter=',')
        fold_item_init_dist[f] = {}
        for i, col in enumerate(distances):
            fold_item_init_dist[f][i] = [parse_distance(x) for x in distances[col].tolist()]
    else:
        initial_pred_list[f] = read_java_output(f)
        # Read in which test items were used
        personal_models[f] = copy.deepcopy(user_sums)
        personal_model(f)

        fold_item_init_dist[f] = {}
        for i, r in enumerate(initial_pred_list[f]):
            fold_item_init_dist[f][i] = []
            contexts = extract_contexts(r)
            initial_preds = extract_predictions(r)
            for recommend in initial_preds:
                fold_item_init_dist[f][i].append(calculate_distance(contexts, recommend, f, r[0]))

        gb_df = pd.DataFrame.from_dict(fold_item_init_dist[f])
        gb_df.to_csv(f'{input_rec_path}{f}-personal-distance.csv', index=False, float_format='%.5f')
    print(f'Distances calculated for {f}, in {time.time() - start_time} s')

if path.isfile(f'{input_rec_path}{algoName}-personal-general.csv'):
    general_df = pd.read_csv(f'{input_rec_path}{algoName}-personal-general.csv', delimiter=',')
    max_dist = general_df['max_dist'].values[0]
    min_dist = general_df['min_dist'].values[0]
    max_init = general_df['max_init'].values[0]
    min_init = general_df['min_init'].values[0]
    div_factor_distances = general_df['div_factor_distances'].values[0]
    div_factor_all_ratings = general_df['div_factor_all_ratings'].values[0]
else:
    max_dist = max(all_distances)
    min_dist = min(all_distances)
    max_init = max(all_initial_ratings)
    min_init = min(all_initial_ratings)
    div_factor_distances = (sum(all_distances) / len(all_distances)) / 0.5
    div_factor_all_ratings = (sum(all_initial_ratings) / len(all_initial_ratings)) / 0.5

    general_dict = {'max_dist': [max_dist], 'min_dist': [min_dist], 'max_init': [max_init], 'min_init': [min_init],
                    'div_factor_distances': [div_factor_distances], 'div_factor_all_ratings': [div_factor_all_ratings]}
    general_df = pd.DataFrame.from_dict(general_dict)
    general_df.to_csv(f'{input_rec_path}{algoName}-personal-general.csv', index=False)

# norm_distances_inits(max_dist, min_dist, all_distances, max_init, min_init, all_initial_ratings)

# Removes all initial recommendations that do not contain any positive item
# def keep_pos_only(all_correct, all_old, all_new):
#     all_correct_new = []
#     all_old_new = []
#     all_new_new = {0: [], 0.1: [], 0.2: [], 0.3: [], 0.4: [], 0.5: [], 0.6: [], 0.7: [], 0.8: [], 0.9: [], 1: []}
#     for indx, list_pos_items in enumerate(all_correct):
#         contains_pos = False
#         for pos_item in list_pos_items:
#             if pos_item in all_old[indx]:
#                 contains_pos = True
#         if contains_pos:
#             all_correct_new.append(list_pos_items)
#             all_old_new.append(all_old[indx])
#             for l in lambdas:
#                 all_new_new[l].append(all_new[l][indx])
#
#     return all_correct_new, all_old_new, all_new_new

def print_rerank_count(all_old, all_new):
    print(f'fold {f} | Old: {len(all_old)} | New: {len(all_new[1])}')
    print(all_correct[f])
    print(all_old)
    print(all_new[1])
    print()

def filter_sames(all_old, all_new, all_correct):
    new_old = []
    new_new = {0: [], 0.1: [], 0.2: [], 0.3: [], 0.4: [], 0.5: [], 0.6: [], 0.7: [], 0.8: [], 0.9: [], 1: []}
    new_correct = []
    for i, l in enumerate(all_old):
        if l != all_new[1][i]:
            new_old.append(l)
            new_correct.append(all_correct[i])
            for la in lambdas:
                new_new[la].append(all_new[la][i])
    return new_old, new_new, new_correct

# Re-rank for each lambda value
if metric_to_use == 'Prec':
    measures = {f'{metric_to_use}_{metrics_sizes[0]}_initial': [], f'{metric_to_use}_{metrics_sizes[0]}_rerank': [],
                f'{metric_to_use}_{metrics_sizes[1]}_initial': [], f'{metric_to_use}_{metrics_sizes[1]}_rerank': []}
else:
    measures = {f'{metric_to_use}_{metrics_sizes[0]}_initial': [], f'{metric_to_use}_{metrics_sizes[0]}_rerank': [],
             f'{metric_to_use}_{metrics_sizes[1]}_initial': [], f'{metric_to_use}_{metrics_sizes[1]}_rerank': [],
             f'{metric_to_use}_{metrics_sizes[2]}_initial': [], f'{metric_to_use}_{metrics_sizes[2]}_rerank': []}

measures_lambda = {0: copy.deepcopy(measures), 0.1: copy.deepcopy(measures), 0.2: copy.deepcopy(measures),
                   0.3: copy.deepcopy(measures), 0.4: copy.deepcopy(measures), 0.5: copy.deepcopy(measures),
                   0.6: copy.deepcopy(measures), 0.7: copy.deepcopy(measures), 0.8: copy.deepcopy(measures),
                   0.9: copy.deepcopy(measures), 1: copy.deepcopy(measures)}

# Go through each fold
for f in folds:
    if path.isfile(f'{input_rec_path}{algoName}-personal-general.csv'):
        initial_pred_list[f] = read_java_output(f)
    for i, r in enumerate(initial_pred_list[f]):
        save_correct_items(f, r)
        re_rank(extract_predictions(r), fold_item_init_dist[f][i])

        # print(f'Fold {f}')
        # print(f'Old ranking: {all_old}')
        # print(f'New ranking: {all_new}')
        # print(f'Correct items: {all_correct[f]}')
        # print()

    # Calculating all metrics
    if len(all_correct) > 0:
        # print_rerank_count(all_old, all_new)
        # all_old, all_new, all_correct[f] = filter_sames(all_old, all_new, all_correct[f])
        # print_rerank_count(all_old, all_new)
        # if metrics_positive_only:
        #     all_correct[f], all_old, all_new = keep_pos_only(all_correct[f], all_old, all_new)

        if metric_to_use == 'MAP':
            for l in lambdas:
                measures_lambda[l][f'{metric_to_use}_{metrics_sizes[0]}_initial'].append(
                    mapk(all_old, all_correct[f], metrics_sizes[0]))
                measures_lambda[l][f'{metric_to_use}_{metrics_sizes[0]}_rerank'].append(
                    mapk(all_new[l], all_correct[f], metrics_sizes[0]))
                measures_lambda[l][f'{metric_to_use}_{metrics_sizes[1]}_initial'].append(
                    mapk(all_old, all_correct[f], metrics_sizes[1]))
                measures_lambda[l][f'{metric_to_use}_{metrics_sizes[1]}_rerank'].append(
                    mapk(all_new[l], all_correct[f], metrics_sizes[1]))
                measures_lambda[l][f'{metric_to_use}_{metrics_sizes[2]}_initial'].append(
                    mapk(all_old, all_correct[f], test_item_amount))
                measures_lambda[l][f'{metric_to_use}_{metrics_sizes[2]}_rerank'].append(
                    mapk(all_new[l], all_correct[f], test_item_amount))
        elif metric_to_use == 'NDCG':
            for l in lambdas:
                measures_lambda[l][f'{metric_to_use}_{metrics_sizes[0]}_initial'].append(
                    ndcg_at(all_old, all_correct[f], metrics_sizes[0]))
                measures_lambda[l][f'{metric_to_use}_{metrics_sizes[0]}_rerank'].append(
                    ndcg_at(all_new[l], all_correct[f], metrics_sizes[0]))
                measures_lambda[l][f'{metric_to_use}_{metrics_sizes[1]}_initial'].append(
                    ndcg_at(all_old, all_correct[f], metrics_sizes[1]))
                measures_lambda[l][f'{metric_to_use}_{metrics_sizes[1]}_rerank'].append(
                    ndcg_at(all_new[l], all_correct[f], metrics_sizes[1]))
                measures_lambda[l][f'{metric_to_use}_{metrics_sizes[2]}_initial'].append(
                    ndcg_at(all_old, all_correct[f], test_item_amount))
                measures_lambda[l][f'{metric_to_use}_{metrics_sizes[2]}_rerank'].append(
                    ndcg_at(all_new[l], all_correct[f], test_item_amount))
        elif metric_to_use == 'Prec':
            for l in lambdas:
                measures_lambda[l][f'{metric_to_use}_{metrics_sizes[0]}_initial'].append(
                    precision_at(all_old, all_correct[f], metrics_sizes[0]))
                measures_lambda[l][f'{metric_to_use}_{metrics_sizes[0]}_rerank'].append(
                    precision_at(all_new[l], all_correct[f], metrics_sizes[0]))
                measures_lambda[l][f'{metric_to_use}_{metrics_sizes[1]}_initial'].append(
                    precision_at(all_old, all_correct[f], metrics_sizes[1]))
                measures_lambda[l][f'{metric_to_use}_{metrics_sizes[1]}_rerank'].append(
                    precision_at(all_new[l], all_correct[f], metrics_sizes[1]))

    all_old = []
    all_new = {0: [], 0.1: [], 0.2: [], 0.3: [], 0.4: [], 0.5: [], 0.6: [], 0.7: [], 0.8: [], 0.9: [], 1: []}


# Printing summary of all metrics after each lambda iteration
def print_all_measures():
    for lmb in lambdas:
        print(f'Lambda {lmb}')
        final['lambda'].append(lmb)
        for key in measures_lambda[lmb]:
            print(f'{key}: {mean(measures_lambda[lmb][key])}')
            final[key].append(mean(measures_lambda[lmb][key]))
        print(f'Total runtime: {time.time() - start_time} s')
        print()


print_all_measures()

# Write resulting file
df = pd.DataFrame.from_dict(final)
res_name = f'personal-{algoName}-{metric_to_use}'
if dist_metric == 'cosine':
    res_name += '-cos'
if dimension != '':
    res_name += f'-{dimension}'

df.to_csv(f'res\\{d_set}\\{test_item_amount}\\{res_name}.csv', index=False, float_format='%.5f')
