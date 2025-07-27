import os
import soundfile as sf
import json
import click
import re
import pandas as pd
from glob import glob
from functools import partial
from multiprocess import Pool
from tqdm import tqdm
import time

def chunks(l, devices):
    chunk_size = len(l) // len(devices)
    remainder = len(l) % len(devices)
    start = 0
    for i in range(len(devices)):
        extra = 1 if i < remainder else 0
        end = start + chunk_size + extra
        yield (l[start:end], devices[i])
        start = end

def loop(
    indices_device_pair,
    file,
    folder,
    language,
):
    indices, device = indices_device_pair
    os.environ['CUDA_VISIBLE_DEVICES'] = str(device)
    
    import torch

    torch.set_grad_enabled(False)

    import torchaudio
    from ctc_forced_aligner import (
        load_audio,
        load_alignment_model,
        generate_emissions,
        preprocess_text,
        get_alignments,
        get_spans,
        postprocess_results,
    )
    device = 'cuda'
    alignment_model, alignment_tokenizer = load_alignment_model(
        device,
        dtype=torch.float16 if device == "cuda" else torch.float32,
    )

    with open(file) as fopen:
        rows = json.load(fopen)

    for i in tqdm(indices):
        filename = os.path.join(folder, f'{i}.json')

        try:
            with open(filename) as fopen:
                json.load(fopen)
            continue
        except:
            pass

        row = rows[i]
        gen_text = row['pronunciation']
        y, sr = sf.read(row['audio_filename'])
        new_wav = torch.from_numpy(y)
        audio_waveform = torchaudio.functional.resample(
            new_wav, orig_freq=44100, new_freq=16000
        ).type(torch.float16).cuda()
        emissions, stride = generate_emissions(
            alignment_model, audio_waveform, batch_size=1
        )
        tokens_starred, text_starred = preprocess_text(
            gen_text,
            romanize=True,
            language=language,
        )
        segments, scores, blank_token = get_alignments(
            emissions,
            tokens_starred,
            alignment_tokenizer,
        )
        spans = get_spans(tokens_starred, segments, blank_token)
        word_timestamps = postprocess_results(text_starred, spans, stride, scores)
        with open(filename, 'w') as fopen:
            json.dump(word_timestamps, fopen)

@click.command()
@click.option('--file')
@click.option('--folder')
@click.option('--replication', default = 1)
@click.option('--language', default = 'ms')
def main(
    file, 
    folder,
    replication,
    language,
):
    devices = os.environ.get('CUDA_VISIBLE_DEVICES')
    if devices is None:
        
        import torch
        devices = list(range(torch.cuda.device_count()))
    else:
        devices = [d.strip() for d in devices.split(',')]

    devices = replication * devices
    print(devices)

    os.makedirs(folder, exist_ok = True)
    with open(file) as fopen:
        rows = json.load(fopen)
    indices = list(range(0, len(rows), 1))
    indices = [i for i in indices if not os.path.exists(os.path.join(folder, f'{i}.json'))]
    print(len(indices))

    df_split = list(chunks(indices, devices))

    loop_partial = partial(
        loop,
        file=file,
        folder=folder,
        language=language,
    )

    with Pool(len(devices)) as pool:
        pooled = pool.map(loop_partial, df_split)

if __name__ == '__main__':
    main()

    