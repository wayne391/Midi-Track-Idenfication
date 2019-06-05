'''
Latest
Modified from pretty_midi: all tempi stored in tick (symolic timing)
KEY: keep it simple and less modification
'''

import mido
import warnings
import functools
import collections
import numpy as np
from .containers import KeySignature, TimeSignature, Lyric, Note, PitchBend, ControlChange, Instrument, TempoChange
from miditoolkit.pianoroll.converter import convert_note_stream_to_pianoroll

DEFAULT_TEMPO = int(500000)

class MidiFile(object):
    def __init__(self, midi_file=None):
        
        # create empty file
        if midi_file is None:
            self.ticks_per_beat = 480
            self.max_tick = 0
            self.tempo_changes = []
            self.time_signature_changes = []
            self.key_signature_changes = []
            self.lyrics = []
            self.instruments = []
            return 

        # load
        if isinstance(midi_file, str):
            # filename
            mido_obj = mido.MidiFile(filename=midi_file)
        else:
            # mido obj
            mido_obj = midi_file

        # ticks_per_beat
        self.ticks_per_beat =  mido_obj.ticks_per_beat

        # convert delta time to cumulative time
        mido_obj = self._convert_delta_to_cumulative(mido_obj)

        # tempo
        self.tempo_changes = self._load_tempo_changes(mido_obj)

        # Update the array which maps ticks to time
        self.max_tick = max([max([e.time for e in t]) for t in mido_obj.tracks]) + 1

        # Populate the list of key and time signature changes
        self.key_signature_changes, self.time_signature_changes = self._load_metadata(mido_obj)
        self.lyrics = self._load_lyrics(mido_obj)

        # sort
        self.time_signature_changes.sort(key=lambda ts: ts.time)
        self.key_signature_changes.sort(key=lambda ks: ks.time)
        self.lyrics.sort(key=lambda lyc: lyc.time)

        # Populate the list of instruments
        self.instruments = self._load_instruments(mido_obj)


    def _convert_delta_to_cumulative(self, mido_obj):
        for track in mido_obj.tracks:
            tick = int(0)
            for event in track:
                event.time += tick
                tick = event.time
        return mido_obj

    def _load_tempo_changes(self, mido_obj):
        tempo_changes = [TempoChange(DEFAULT_TEMPO, 0)]

        # traversing all tracks to seek messages
        for track in mido_obj.tracks:
            for event in track:
                if event.type == 'set_tempo':
                    tempo = event.tempo
                    tick = event.time
                    if tick == 0:
                        tempo_changes = [TempoChange(tempo, 0)]
                    else:
                        last_tempo = tempo_changes[-1].tempo
                        if tempo != last_tempo:
                            tempo_changes.append(TempoChange(tempo, tick))
        return tempo_changes

    def _load_metadata(self, mido_obj):
        # metadata: key and time signature
        key_signature_changes = []
        time_signature_changes = []

        # traversing all tracks to seek messagess
        for track in mido_obj.tracks:
            for event in track:
                if event.type == 'key_signature':
                    key_obj = KeySignature(event.key, event.time)
                    key_signature_changes.append(key_obj)

                elif event.type == 'time_signature':
                    ts_obj = TimeSignature(event.numerator,
                                        event.denominator,
                                        event.time)
                    time_signature_changes.append(ts_obj)
        return key_signature_changes, time_signature_changes, 

    def _load_lyrics(self, mido_obj):
        lyrics = []
        # traversing all tracks to seek messagess
        for track in mido_obj.tracks:
            for event in track:
                if event.type == 'lyrics':
                    lyrics.append(Lyric(event.text, event.time))
        return lyrics

    def _load_instruments(self, midi_data):
        instrument_map = collections.OrderedDict()
        # Store a similar mapping to instruments storing "straggler events",
        # e.g. events which appear before we want to initialize an Instrument
        stragglers = {}
        # This dict will map track indices to any track names encountered
        track_name_map = collections.defaultdict(str)

        def __get_instrument(program, channel, track, create_new):
            """Gets the Instrument corresponding to the given program number,
            drum/non-drum type, channel, and track index.  If no such
            instrument exists, one is created.

            """
            # If we have already created an instrument for this program
            # number/track/channel, return it
            if (program, channel, track) in instrument_map:
                return instrument_map[(program, channel, track)]
            # If there's a straggler instrument for this instrument and we
            # aren't being requested to create a new instrument
            if not create_new and (channel, track) in stragglers:
                return stragglers[(channel, track)]
            # If we are told to, create a new instrument and store it
            if create_new:
                is_drum = (channel == 9)
                instrument = Instrument(
                    program, is_drum, track_name_map[track_idx])
                # If any events appeared for this instrument before now,
                # include them in the new instrument
                if (channel, track) in stragglers:
                    straggler = stragglers[(channel, track)]
                    instrument.control_changes = straggler.control_changes
                    instrument.pitch_bends = straggler.pitch_bends
                # Add the instrument to the instrument map
                instrument_map[(program, channel, track)] = instrument
            # Otherwise, create a "straggler" instrument which holds events
            # which appear before we actually want to create a proper new
            # instrument
            else:
                # Create a "straggler" instrument
                instrument = Instrument(program, track_name_map[track_idx])
                # Note that stragglers ignores program number, because we want
                # to store all events on a track which appear before the first
                # note-on, regardless of program
                stragglers[(channel, track)] = instrument
            return instrument

        for track_idx, track in enumerate(midi_data.tracks):
            # Keep track of last note on location:
            # key = (instrument, note),
            # value = (note-on tick, velocity)
            last_note_on = collections.defaultdict(list)
            # Keep track of which instrument is playing in each channel
            # initialize to program 0 for all channels
            current_instrument = np.zeros(16, dtype=np.int)
            for event in track:
                # Look for track name events
                if event.type == 'track_name':
                    # Set the track name for the current track
                    track_name_map[track_idx] = event.name
                # Look for program change events
                if event.type == 'program_change':
                    # Update the instrument for this channel
                    current_instrument[event.channel] = event.program
                # Note ons are note on events with velocity > 0
                elif event.type == 'note_on' and event.velocity > 0:
                    # Store this as the last note-on location
                    note_on_index = (event.channel, event.note)
                    last_note_on[note_on_index].append((
                        event.time, event.velocity))
                # Note offs can also be note on events with 0 velocity
                elif event.type == 'note_off' or (event.type == 'note_on' and
                                                  event.velocity == 0):
                    # Check that a note-on exists (ignore spurious note-offs)
                    key = (event.channel, event.note)
                    if key in last_note_on:
                        # Get the start/stop times and velocity of every note
                        # which was turned on with this instrument/drum/pitch.
                        # One note-off may close multiple note-on events from
                        # previous ticks. In case there's a note-off and then
                        # note-on at the same tick we keep the open note from
                        # this tick.
                        end_tick = event.time
                        open_notes = last_note_on[key]

                        notes_to_close = [
                            (start_tick, velocity)
                            for start_tick, velocity in open_notes
                            if start_tick != end_tick]
                        notes_to_keep = [
                            (start_tick, velocity)
                            for start_tick, velocity in open_notes
                            if start_tick == end_tick]

                        for start_tick, velocity in notes_to_close:
                            start_time = start_tick
                            end_time = end_tick
                            # Create the note event
                            note = Note(velocity, event.note, start_time,
                                        end_time)
                            # Get the program and drum type for the current
                            # instrument
                            program = current_instrument[event.channel]
                            # Retrieve the Instrument instance for the current
                            # instrument
                            # Create a new instrument if none exists
                            instrument = __get_instrument(
                                program, event.channel, track_idx, 1)
                            # Add the note event
                            instrument.notes.append(note)

                        if len(notes_to_close) > 0 and len(notes_to_keep) > 0:
                            # Note-on on the same tick but we already closed
                            # some previous notes -> it will continue, keep it.
                            last_note_on[key] = notes_to_keep
                        else:
                            # Remove the last note on for this instrument
                            del last_note_on[key]
                # Store pitch bends
                elif event.type == 'pitchwheel':
                    # Create pitch bend class instance
                    bend = PitchBend(event.pitch, event.time)
                    # Get the program for the current inst
                    program = current_instrument[event.channel]
                    # Retrieve the Instrument instance for the current inst
                    # Don't create a new instrument if none exists
                    instrument = __get_instrument(
                        program, event.channel, track_idx, 0)
                    # Add the pitch bend event
                    instrument.pitch_bends.append(bend)
                # Store control changes
                elif event.type == 'control_change':
                    control_change = ControlChange(
                        event.control, event.value, event.time)
                    # Get the program for the current inst
                    program = current_instrument[event.channel]
                    # Retrieve the Instrument instance for the current inst
                    # Don't create a new instrument if none exists
                    instrument = __get_instrument(
                        program, event.channel, track_idx, 0)
                    # Add the control change event
                    instrument.control_changes.append(control_change)
        # Initialize list of instruments from instrument_map
        instruments = [i for i in instrument_map.values()]
        return instruments
    
    def get_tick_to_time_mapping(self):
        return get_tick_to_time_mapping(
            self.ticks_per_beat, 
            self.max_tick, 
            self.tempo_changes)

    def get_instrument_pianoroll(
            self, 
            instrument_idx,
            binary_thres=None,
            resample_resolution=None, 
            resample_method=round):
            
        return convert_note_stream_to_pianoroll(
            self.instruments[instrument_idx].notes,
            self.ticks_per_beat,
            resample_resolution=resample_resolution, 
            resample_method=resample_method,
            binary_thres=binary_thres,
            max_tick=self.max_tick)

    def __repr__(self):
        output_list = [
            "Ticks per beat: {}".format(self.ticks_per_beat),
            "Max tick: {}".format(self.max_tick),
            "Tempo changes: {}".format(self.tempo_changes),
            "Time sig: {}".format(self.time_signature_changes),
            "Key sig: {}".format(self.key_signature_changes),
            "Lyrics: {}".format(bool(len(self.lyrics))),
            "Instruments: {}".format(len(self.instruments))
        ]
        output_str = "\n".join(output_list)
        return output_str

    def __str__(self):
        output_list = [
            "Ticks per beat: {}".format(self.ticks_per_beat),
            "Max tick: {}".format(self.max_tick),
            "Tempo changes: {}".format(self.tempo_changes),
            "Time sig: {}".format(self.time_signature_changes),
            "Key sig: {}".format(self.key_signature_changes),
            "Lyrics: {}".format(bool(len(self.lyrics))),
            "Instruments: {}".format(len( self.instruments))
        ] 
        output_str = "\n".join(output_list)
        return output_str

    def dump(self, filename='res.mid', segment=None, shift=True, instrument_idx=None):
        def event_compare(event1, event2):
            secondary_sort = {
                'set_tempo': lambda e: (1 * 256 * 256),
                'time_signature': lambda e: (2 * 256 * 256),
                'key_signature': lambda e: (3 * 256 * 256),
                'lyrics': lambda e: (4 * 256 * 256),
                'program_change': lambda e: (5 * 256 * 256),
                'pitchwheel': lambda e: ((6 * 256 * 256) + e.pitch),
                'control_change': lambda e: (
                    (7 * 256 * 256) + (e.control * 256) + e.value),
                'note_off': lambda e: ((8 * 256 * 256) + (e.note * 256)),
                'note_on': lambda e: (
                    (9 * 256 * 256) + (e.note * 256) + e.velocity),
                'end_of_track': lambda e: (10 * 256 * 256)
            }
            if (event1.time == event2.time and
                    event1.type in secondary_sort and
                    event2.type in secondary_sort):
                return (secondary_sort[event1.type](event1) -
                        secondary_sort[event2.type](event2))
            return event1.time - event2.time
        if instrument_idx is None:
            pass
        if len(instrument_idx)==0:
            return
        elif isinstance(instrument_idx, int):
            instrument_idx = [instrument_idx]
        elif isinstance(instrument_idx, list):
            pass
        else:
            raise ValueError('Invalid instrument index')
        # boundary
        if segment is not None:
            if not isinstance(segment, list) and not isinstance(segment, tuple):
                raise ValueError('Invalid segment info')

            st = segment[0]
            ed = segment[1]
  
            if type(st) != type(ed):
                raise ValueError('Type inconsistency')

            if isinstance(st, float):
                # second
                tick_to_time = self.get_tick_to_time_mapping()
                start_tick = get_tick_index_by_seconds(st, tick_to_time)
                end_tick = get_tick_index_by_seconds(ed, tick_to_time)

            if isinstance(st, int):
                # tick
                start_tick = st
                end_tick = ed

        # Create file
        midi_parsed = mido.MidiFile(ticks_per_beat=self.ticks_per_beat)

        # Create track 0 with timing information
        meta_track = mido.MidiTrack()

        # {meta track}
        # 1. Time signature
        # add default
        add_ts = True
        ts_list = []
        if self.time_signature_changes:
            add_ts = min([ts.time for ts in self.time_signature_changes]) > 0.0
        if add_ts:
            ts_list.append(mido.MetaMessage(
                'time_signature', 
                time=0, 
                numerator=4, 
                denominator=4))
        # add each
        for ts in self.time_signature_changes:
            ts_list.append(
                mido.MetaMessage(
                    'time_signature', 
                    time=ts.time,
                    numerator=ts.numerator, 
                    denominator=ts.denominator))
        
        # 2. Tempo
        # add default
        add_t = True
        tempo_list = [] 
        if self.tempo_changes:
            add_t = min([t.time for t in self.tempo_changes]) > 0.0
        if add_t:
            tempo_list.append(
                mido.MetaMessage(
                    'set_tempo', 
                    time=0, 
                    tempo=DEFAULT_TEMPO))
        # add each
        for t in self.tempo_changes:
            tempo_list.append(
                mido.MetaMessage(
                    'set_tempo',
                    time=t.time,
                    tempo=int(t.tempo)))
        
        # 3. Lyrics
        lyrics_list = []
        for l in self.lyrics:
            lyrics_list.append(
                mido.MetaMessage(
                    'lyrics', 
                    time=l.time, 
                    text=l.text))   
        
        # 4. Key
        key_number_to_mido_key_name = [
            'C', 'Db', 'D', 'Eb', 'E', 'F', 'F#', 'G', 'Ab', 'A', 'Bb', 'B',
            'Cm', 'C#m', 'Dm', 'D#m', 'Em', 'Fm', 'F#m', 'Gm', 'G#m', 'Am',
            'Bbm', 'Bm']
        key_list = []
        for ks in self.key_signature_changes:
            key_list.append(mido.MetaMessage(
                'key_signature', time=ks.time,
                key=key_number_to_mido_key_name[ks.key_number]))

        if segment:
            ts_list = include_meta_events_within_range(ts_list, start_tick, end_tick, shift=shift, front=True)
            tempo_list = include_meta_events_within_range(tempo_list, start_tick, end_tick, shift=shift, front=True)
            lyrics_list = include_meta_events_within_range(lyrics_list, start_tick, end_tick, shift=shift, front=False)
            key_list = include_meta_events_within_range(key_list, start_tick, end_tick, shift=shift, front=True)

        meta_track = ts_list + tempo_list + lyrics_list + key_list

        # sort
        meta_track.sort(key=functools.cmp_to_key(event_compare))

        # end of meta track
        meta_track.append(mido.MetaMessage(
            'end_of_track', time=meta_track[-1].time + 1))
        midi_parsed.tracks.append(meta_track) 

        # {instruments}
        channels = list(range(16))
        channels.remove(9)  # for durm

        for cur_idx, instrument in enumerate(self.instruments):
            if instrument_idx:
                if cur_idx not in instrument_idx:
                    continue

            track = mido.MidiTrack()
            # segment-free
            # track name
            if instrument.name:
                track.append(mido.MetaMessage(
                    'track_name', time=0, name=instrument.name))

            # If it's a drum event, we need to set channel to 9
            if instrument.is_drum:
                channel = 9
            # Otherwise, choose a channel from the possible channel list
            else:
                channel = channels[cur_idx % len(channels)]
            # Set the program number
            track.append(mido.Message(
                'program_change', time=0, program=instrument.program,
                channel=channel))
            
            # segment-related
            # Add all pitch bend events
            bend_list = []
            for bend in instrument.pitch_bends:
                bend_list.append(mido.Message(
                    'pitchwheel', time=bend.time,
                    channel=channel, pitch=bend.pitch))
           
            # Add all control change events
            cc_list = []
            for control_change in instrument.control_changes:
                cc_list.append(mido.Message(
                    'control_change',
                    time=control_change.time,
                    channel=channel, control=control_change.number,
                    value=control_change.value))

            if segment:
                bend_list = include_meta_events_within_range(bend_list, start_tick, end_tick, shift=shift, front=True)
                cc_list = include_meta_events_within_range(cc_list, start_tick, end_tick, shift=shift, front=True)
            track += (bend_list + cc_list)

            # Add all note events
            for note in instrument.notes:
                # Construct the note-on event
                if segment:
                    note = check_note_within_range(note, start_tick, end_tick, shift=True)
                if note:
                    track.append(mido.Message(
                        'note_on', time=note.start,
                        channel=channel, note=note.pitch, velocity=note.velocity))
                    # Also need a note-off event (note on with velocity 0)
                    track.append(mido.Message(
                        'note_on', time=note.end,
                        channel=channel, note=note.pitch, velocity=0))
            track = sorted(track, key=functools.cmp_to_key(event_compare))

            # If there's a note off event and a note on event with the same
            # tick and pitch, put the note off event first
            for n, (event1, event2) in enumerate(zip(track[:-1], track[1:])):
                if (event1.time == event2.time and
                        event1.type == 'note_on' and
                        event2.type == 'note_on' and
                        event1.note == event2.note and
                        event1.velocity != 0 and
                        event2.velocity == 0):
                    track[n] = event2
                    track[n + 1] = event1

            # Finally, add in an end of track event
            track.append(mido.MetaMessage(
                'end_of_track', time=track[-1].time + 1))
            # Add to the list of output tracks
            midi_parsed.tracks.append(track)

        # Cumulative timing to delta
        for track in midi_parsed.tracks:
            tick = 0
            for event in track:
                event.time -= tick
                tick += event.time

        # Write it out
        midi_parsed.save(filename=filename)


def check_note_within_range(note, st, ed, shift=True):
    tmp_st = max(st, note.start)
    tmp_ed = max(st, min(note.end, ed))

    if (tmp_ed - tmp_st) <= 0:
        return None
    if shift:
        tmp_st -= st
        tmp_ed -= st
    note.start = int(tmp_st)
    note.end = int(tmp_ed)
    return note


def include_meta_events_within_range(events, st, ed, shift=True, front=True):
    '''
    For time, key signatutr
    '''
    proc_events = []
    num = len(events)

    if not events:
        return events
        
    # include events from back
    for i in range(num - 1, -1, -1):
        event = events[i]
        if event.time < st:
            break
        if event.time < ed:
            proc_events.append(event)
    
    # if the first tick has no event, add the previous one
    if front:
        if not proc_events:
            proc_events = events[i]

        elif proc_events[-1].time != st:
            proc_events.append(events[i])
        else:
            pass

    # reverse
    proc_events = proc_events[::-1]

    # shift
    result = []
    shift = st if shift else 0
    for event in proc_events:
        event.time -= st
        event.time = int(event.time)
        result.append(event)
    return result
        

def _find_nearest_np(array, value):
    return (np.abs(array - value)).argmin()


def get_tick_index_by_seconds(sec, tick_to_time):
    if not isinstance(sec, float):
        raise ValueError('Seconds should be float')

    if isinstance(sec, list) or isinstance(sec, tuple):
        return [_find_nearest_np(tick_to_time, s) for s in sec]
    else:
        return _find_nearest_np(tick_to_time, sec)


def get_tick_to_time_mapping(ticks_per_beat, max_tick, tempo_changes):
    tick_to_time = np.zeros(max_tick + 1)
    num_tempi = len(tempo_changes)

    fianl_tick = max_tick
    acc_time = 0

    for idx in range(num_tempi):
        start_tick = tempo_changes[idx].time
        cur_tempo = tempo_changes[idx].tempo

        # compute tick scale
        seconds_per_beat = cur_tempo / 1000000.0
        seconds_per_tick = seconds_per_beat / float(ticks_per_beat)

        # set end tick of interval
        end_tick = tempo_changes[idx + 1].time if (idx + 1) < num_tempi else fianl_tick

        # wrtie interval
        ticks = np.arange(end_tick - start_tick + 1)
        tick_to_time[start_tick:end_tick + 1] = (acc_time + seconds_per_tick *ticks)
        acc_time = tick_to_time[end_tick]
    return tick_to_time
