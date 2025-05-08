
# Mainly authored by Perplexity AI in May 2025

# Currently, this only works with blocking a mid/low mixup; anything else
#   would require moderate modification

import sys
import random
import time
import datetime
from os import _exit, path, makedirs
from scipy.stats import binom # to analyze results
from PyQt5.QtWidgets import QApplication, QMainWindow, QLabel
from PyQt5.QtMultimedia import QMediaPlayer, QMediaContent, QSoundEffect
from PyQt5.QtMultimediaWidgets import QVideoWidget
from PyQt5.QtCore import Qt, QUrl, QTimer
from PyQt5.QtGui import QPixmap, QFont

# === CONFIGURABLE PARAMETERS ===
# General
OVERLAY_SCALE = 1.0          # Overlay scale
ROLLING_WINDOWS = [1, 5, 10, 25, 100, 999] # Stats windows
LOW_REQUIRED_FRAME = 60 + 18 # Earliest correct frame for "low" (target=18f)
BREAK_WINDOW_MS = 500        # ms to show overlay before next trial
DEFAULT_RES = (1024, 576)    # w,h; default (non-fullscreen) window resolution
FULLSCREEN = True           # start in fullscreen?

# Video info (make sure these are correct for the videos you use!)
REACTION_START_TIME = 1.0    # Time before reaction allowed (s)
FPS = 60                     # Video FPS
MID_TOTAL_FRAMES = 109       # Frames for "mid" video
LOW_TOTAL_FRAMES = 109       # Frames for "low" video

# Video selection
#VIDEO_FILES = ["video_mid.mp4", "video_low.mp4"] # total frames = 150
#VIDEO_FILES = ["mid_high.mp4", "low_high.mp4"] # total frames = 150
#VIDEO_FILES = ["mid_cutoff.mp4", "low_cutoff.mp4"] # total frames = 109
VIDEO_FILES = ["duomo_cutoff_mid.mp4", "duomo_cutoff_low.mp4"] # total frames = 109
# === END CONFIGURABLE PARAMETERS ===

BASE_OVERLAY_SIZE = 200     # Base overlay size in px - DO NOT CHANGE

# This is used to test whether the rate of blocking is statistically significant
""""Calculate the statistical significance (p-value) and its complement for a
    binomial outcome, given the number of trials, observed successes, and
    expected probability under the null hypothesis."""
def calculate_confidence(chances: int, hits: int, odds: float):
    # Returns: tuple[confidence: float, p_value: float]
    observed_pmf = binom.pmf(hits, chances, odds)
    p_value = sum(
        binom.pmf(k, chances, odds)
        for k in range(chances + 1)
        if binom.pmf(k, chances, odds) <= observed_pmf
    )
    confidence = 1 - p_value
    return (confidence, p_value)

class SimpleReactionTest(QMainWindow):
    def __init__(self, video_paths):
        super().__init__()
        self.setWindowTitle("Reaction Tester")
        self.resize(*DEFAULT_RES)
        self.video_paths = video_paths

        # State
        self.fullscreen = FULLSCREEN
        self.pause_after_video = False
        self.waiting_for_space = True   # Start paused
        self.priming_index = 0
        self.priming = True

        # Video
        self.fps = FPS
        self.mid_total_frames = MID_TOTAL_FRAMES
        self.low_total_frames = LOW_TOTAL_FRAMES
        self.low_required_frame = LOW_REQUIRED_FRAME
        self.target_adj_RT = (self.low_required_frame - 60) / 60 * 1000
        self.reaction_start_time = REACTION_START_TIME

        # Sizing
        self.overlay_size = int(BASE_OVERLAY_SIZE * OVERLAY_SCALE)
        self.stats_font_size = int(self.overlay_size * 0.05)
        self.stats_label_width = int( (5+5+8*(28)) * OVERLAY_SCALE)
        self.stats_label_height = int( (5+5+16*(len(ROLLING_WINDOWS)+1)) * OVERLAY_SCALE)
        self.advanced_stats_font_size = int(self.overlay_size * 0.05)
        self.advanced_stats_label_width = int( (5+5+8*(33)) * OVERLAY_SCALE)
        self.advanced_stats_label_height = int( (5+5+16*6) * OVERLAY_SCALE)

        # Video widgets and players
        self.video_widgets = [QVideoWidget(self) for _ in range(2)]
        self.players = [QMediaPlayer(None, QMediaPlayer.VideoSurface) for _ in range(2)]

        for i in range(2):
            self.players[i].setMedia(QMediaContent(QUrl.fromLocalFile(self.video_paths[i])))
            self.players[i].setVideoOutput(self.video_widgets[i])
            self.video_widgets[i].setGeometry(0, 0, self.width(), self.height())
            self.video_widgets[i].hide()

        # Overlay
        self.overlay_label = QLabel(self)
        self.overlay_label.setStyleSheet("background: transparent;")
        self.overlay_label.setGeometry(
            self.width()//2 - self.overlay_size//2,
            10,
            self.overlay_size,
            self.overlay_size
        )
        self.overlay_label.hide()
        self.check_pixmap = QPixmap("check.png").scaled(
            self.overlay_size, self.overlay_size,
            Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        self.x_pixmap = QPixmap("x.png").scaled(
            self.overlay_size, self.overlay_size,
            Qt.KeepAspectRatio, Qt.SmoothTransformation
        )

        # Stats label
        self.stats_label = QLabel(self)
        font = QFont("Courier New", self.stats_font_size)
        self.stats_label.setFont(font)
        self.stats_label.setStyleSheet("background-color: white; color: black; padding: 3px;")
        self.stats_label.setGeometry(10, 10, self.stats_label_width, self.stats_label_height)
        self.stats_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.stats_label.setWordWrap(True)
        self.stats_label.raise_()

        # Advanced stats tile (below stats label)
        self.advanced_stats_label = QLabel(self)
        self.advanced_stats_label.setFont(font)
        self.advanced_stats_label.setStyleSheet("background-color: white; color: black; padding: 3px;")
        self.advanced_stats_label.setGeometry(
            10,
            10 + self.stats_label_height + 10,
            self.advanced_stats_label_width,
            self.advanced_stats_label_height
        )
        self.advanced_stats_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.advanced_stats_label.setWordWrap(True)
        self.advanced_stats_label.raise_()

        # Sounds
        self.ding_sound = QSoundEffect()
        self.ding_sound.setSource(QUrl.fromLocalFile("ding.wav"))
        self.buzz_sound = QSoundEffect()
        self.buzz_sound.setSource(QUrl.fromLocalFile("buzz.wav"))

        # Trial state
        self.active_index = None
        self.video_ended = False
        self.frame_timer = QTimer()
        self.frame_timer.timeout.connect(self.on_frame_advance)
        self.current_frame = 0
        self.s_pressed = False
        self.s_pressed_frame = None
        self.trial_start_time = None
        self.s_response_time = None

        # Stats
        self.trials = 0
        self.correct = 0
        self.incorrect = 0
        self.correct_rts = []
        self.incorrect_rts = []
        self.trial_history = []

        # Connect signals
        for i, player in enumerate(self.players):
            player.mediaStatusChanged.connect(lambda status, idx=i: self.on_media_status_changed(status, idx))

        self.prime_next_video()  # Preload videos

    def update_stats_label(self):
        def rolling_stats(window):
            recent = self.trial_history[-window:]
            total = len(recent)
            if total == 0:
                return "n/a"
            correct = sum(1 for t in recent if t['correct'])
            incorrect = total - correct
            correct_pct = correct / total * 100# if total else 0
            incorrect_pct = incorrect / total * 100# if total else 0
            correct_rts = [t['rt'] for t in recent if t['rt_type'] == 'correct']
            incorrect_rts = [t['rt'] for t in recent if t['rt_type'] == 'incorrect']
            avg_correct_rt = sum(correct_rts) / len(correct_rts) if correct_rts else 0
            avg_incorrect_rt = sum(incorrect_rts) / len(incorrect_rts) if incorrect_rts else 0
            return (
                f"{correct:>3}/{total:<3} "
                f"{correct_pct:>3.0f}% "
                f"{avg_correct_rt:>4.0f} {avg_incorrect_rt:>4.0f}"
            )
        # create header
        header = " ct    C/I     %   RTc  RTi"
        # create lines
        lines = []
        for w in ROLLING_WINDOWS:
            lines.append(f"{w:>3}: {rolling_stats(w)}")
        text = header + '\n' + '\n'.join(lines)
        self.stats_label.setText(text)
        self.stats_label.raise_()
        self.update_advanced_stats_label()

    # --- NEW: Advanced stats tile update method ---
    def update_advanced_stats_label(self):
        mids_blocked = [t for t in self.trial_history if t.get('event') == 'mid_block']
        mids_ducked = [t for t in self.trial_history if t.get('event') == 'mid_duck']
        lows_blocked = [t for t in self.trial_history if t.get('event') == 'low_block']
        lows_blocked_late = [t for t in self.trial_history if t.get('event') == 'low_block_late']
        lows_missed = [t for t in self.trial_history if t.get('event') == 'low_miss']
        confidence = calculate_confidence(len(self.trial_history), len(mids_blocked)+len(lows_blocked), 0.50)[0]

        def avg_rt(lst):
            rts = [t['rt'] for t in lst if t.get('rt') is not None]
            return f"{sum(rts)/len(rts):.0f}" if rts else "n/a"

        '''text = ( # OLD LAYOUT
            #f"Mids/Lows blocked: {len(mids_blocked)}/{len(lows_blocked)} (+{avg_rt(lows_blocked)})\n"
            #f"Lows blocked late: {len(lows_blocked_late)} (+{avg_rt(lows_blocked_late)})\n"
            #f"Mids ducked: {len(mids_ducked)} (+{avg_rt(mids_ducked)})\n"
            #f"Lows missed: {len(lows_missed)}"
            f"   Blocked-Hit\n"
            f"Mid    {len(mids_blocked):>3}-{len(mids_ducked)}\n"
            f"Low    {len(lows_blocked):>3}-{len(lows_missed)+len(lows_blocked_late)}\n"
            f"{len(lows_blocked_late):>3} ({avg_rt(lows_blocked_late)} ms) late lows\n"
            f"{len(lows_missed):>3} missed lows\n"
            f"{len(mids_ducked):>3} ({avg_rt(mids_ducked):>3} ms) ducked mids\n"
        )'''

        text = (
            f"       |    Mid     |    Low\n"
            f"Blocked|{len(mids_blocked):>3}         |{len(lows_blocked):>3} ({avg_rt(lows_blocked):>3} ms)\n"
            f"Hit    |{len(mids_ducked):>3} ({avg_rt(mids_ducked):>3} ms)|{len(lows_missed)+len(lows_blocked_late):>3}\n"
            f"Missed |            |{len(lows_missed):>3}\n"
            f"Late   |            |{len(lows_blocked_late):>3} ({avg_rt(lows_blocked_late):>3} ms)\n"
            f"Stat. significance={confidence*100:>13.9f}%"
        )
        self.advanced_stats_label.setText(text)
        self.advanced_stats_label.raise_()

    def prime_next_video(self):
        # Preload both videos for smoother start
        if self.priming_index < 2:
            self.video_widgets[self.priming_index].show()
            self.players[self.priming_index].setPosition(0)
            self.players[self.priming_index].play()
        else:
            for vw in self.video_widgets:
                vw.hide()
            self.priming = False
            if self.fullscreen:
                self.showFullScreen()
            else:
                self.showNormal()
            # Start in PAUSED mode; wait for space
            self.waiting_for_space = True

    def on_media_status_changed(self, status, idx):
        if self.priming and idx == self.priming_index:
            if status == QMediaPlayer.LoadedMedia:
                self.players[self.priming_index].setPosition(self.players[self.priming_index].duration() - 10)
            elif status == QMediaPlayer.EndOfMedia:
                self.players[self.priming_index].stop()
                self.video_widgets[self.priming_index].hide()
                self.priming_index += 1
                QTimer.singleShot(10, self.prime_next_video)
            return

        if not self.priming and status == QMediaPlayer.EndOfMedia:
            self.overlay_label.hide()
            self.video_ended = True
            self.frame_timer.stop()
            if self.pause_after_video:
                self.pause_after_video = False
                self.waiting_for_space = True
            else:
                QTimer.singleShot(BREAK_WINDOW_MS, self.start_random_video)

    def start_random_video(self):
        # Pick a random video and reset state
        new_index = random.randint(0, 1)
        if self.active_index is not None:
            self.players[self.active_index].pause()
            self.video_widgets[self.active_index].hide()
        self.active_index = new_index
        self.video_widgets[self.active_index].setGeometry(0, 0, self.width(), self.height())
        self.video_widgets[self.active_index].show()
        self.video_widgets[self.active_index].raise_()
        self.overlay_label.raise_()
        self.stats_label.raise_()
        self.advanced_stats_label.raise_()
        self.players[self.active_index].setPosition(0)
        self.players[self.active_index].play()
        self.video_ended = False
        self.current_frame = 0
        self.s_pressed = False
        self.s_pressed_frame = None
        self.trial_start_time = time.perf_counter()
        self.s_response_time = None
        self.frame_timer.start(int(1000 / self.fps))
        self.reaction_window_start = self.trial_start_time + self.reaction_start_time

    # account for ~3f Py/Qt input lag here!
    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()
        elif event.key() == Qt.Key_F11:
            # Toggle fullscreen at any time
            if self.fullscreen:
                self.showNormal()
                self.fullscreen = False
            else:
                self.showFullScreen()
                self.fullscreen = True
        elif event.key() == Qt.Key_Space:
            if self.waiting_for_space:
                self.waiting_for_space = False
                self.start_random_video()
            elif not self.video_ended:
                self.pause_after_video = True
        elif event.key() == Qt.Key_S and not self.video_ended:
            if not self.s_pressed:
                self.s_pressed = True
                self.s_pressed_frame = self.current_frame
                abs_press_time = time.perf_counter()
                raw_rt = (abs_press_time - self.trial_start_time) * 1000
                # account for Py/Qt input lag here
                adj_rt = (abs_press_time - self.reaction_window_start - 3/60) * 1000
                valid_rt = adj_rt > 0
                if self.active_index == 0:
                    correct_bool = False
                    self.show_overlay(correct=False)
                    self.buzz_sound.play()
                    self.trials += 1
                    self.incorrect += 1
                    if valid_rt:
                        self.incorrect_rts.append(adj_rt)
                    self.trial_history.append({
                        'correct': correct_bool,
                        'rt': adj_rt if valid_rt else None,
                        'rt_type': 'incorrect' if valid_rt else None,
                        'video_type': 'mid',
                        'event': 'mid_duck'
                    })
                    self.update_stats_label()
                elif self.active_index == 1:
                    if adj_rt <= self.target_adj_RT:
                        correct_bool = True
                        self.show_overlay(correct=True)
                        self.ding_sound.play()
                        self.trials += 1
                        self.correct += 1
                        if valid_rt:
                            self.correct_rts.append(adj_rt)
                        self.trial_history.append({
                            'correct': correct_bool,
                            'rt': adj_rt if valid_rt else None,
                            'rt_type': 'correct' if valid_rt else None,
                            'video_type': 'low',
                            'event': 'low_block'
                        })
                        self.update_stats_label()
                    else:
                        correct_bool = False
                        self.show_overlay(correct=False)
                        self.buzz_sound.play()
                        self.trials += 1
                        self.incorrect += 1
                        if valid_rt:
                            self.incorrect_rts.append(adj_rt)
                        self.trial_history.append({
                            'correct': correct_bool,
                            'rt': adj_rt if valid_rt else None,
                            'rt_type': 'incorrect' if valid_rt else None,
                            'video_type': 'low',
                            'event': 'low_block_late'
                        })
                        self.update_stats_label()

    def on_frame_advance(self):
        self.current_frame += 1
        if self.active_index == 0:
            if self.current_frame >= self.mid_total_frames:
                self.frame_timer.stop()
                self.video_ended = True
                if not self.s_pressed:
                    correct_bool = True
                    self.show_overlay(correct=True)
                    self.ding_sound.play()
                    self.trials += 1
                    self.correct += 1
                    self.trial_history.append({
                        'correct': correct_bool,
                        'rt': None,
                        'rt_type': None,
                        'video_type': 'mid',
                        'event': 'mid_block'
                    })
                    self.update_stats_label()
        elif self.active_index == 1:
            if self.current_frame >= self.low_total_frames:
                self.frame_timer.stop()
                self.video_ended = True
                if not self.s_pressed:
                    correct_bool = False
                    self.show_overlay(correct=False)
                    self.buzz_sound.play()
                    self.trials += 1
                    self.incorrect += 1
                    self.trial_history.append({
                        'correct': correct_bool,
                        'rt': None,
                        'rt_type': None,
                        'video_type': 'low',
                        'event': 'low_miss'
                    })
                    self.update_stats_label()

    def show_overlay(self, correct=True):
        # Show check/x overlay, rescale if needed
        self.overlay_size = int(BASE_OVERLAY_SIZE * OVERLAY_SCALE)
        pixmap = (QPixmap("check.png") if correct else QPixmap("x.png")).scaled(
            self.overlay_size, self.overlay_size,
            Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        self.overlay_label.setPixmap(pixmap)
        self.overlay_label.setGeometry(
            self.width()//2 - self.overlay_size//2,
            10,
            self.overlay_size,
            self.overlay_size
        )
        self.overlay_label.show()
        self.overlay_label.raise_()
        self.stats_label.raise_()
        self.advanced_stats_label.raise_()
        QTimer.singleShot(BREAK_WINDOW_MS, self.overlay_label.hide)

    def resizeEvent(self, event):
        for vw in self.video_widgets:
            vw.setGeometry(0, 0, self.width(), self.height())
        self.overlay_label.setGeometry(
            self.width()//2 - self.overlay_size//2,
            10,
            self.overlay_size,
            self.overlay_size
        )
        self.advanced_stats_label.setGeometry(
            10,
            10 + self.stats_label_height + 10,
            self.advanced_stats_label_width,
            self.advanced_stats_label_height
        )
        self.overlay_label.raise_()
        self.stats_label.raise_()
        self.advanced_stats_label.raise_()

    def save_trial_history_to_csv(self):
        print("Starting to save file...")
        if len(self.trial_history) < 2:
            print(f"Not enough guesses to save ({len(self.trial_history)}).")
            return
        # Format: "2025-05-03 12:02 AM"
        now = datetime.datetime.now()
        timestamp = now.strftime("%Y-%m-%d %I;%M %p")
        filename = f"log/guesses_{timestamp}.csv"
        # create the 'log' folder if it doesn't exist
        if not path.exists('log'):
            makedirs('log')
        with open(filename, "w", encoding="utf-8") as f:
            print("Opened...")
            # Write header
            fieldnames = list(self.trial_history[0].keys())
            f.write(",".join(fieldnames) + "\n")
            # Write each row
            for row in self.trial_history:
                # Escape commas and quotes if needed
                values = []
                for field in fieldnames:
                    value = str(row[field])
                    if ',' in value or '"' in value:
                        value = '"' + value.replace('"', '""') + '"'
                    values.append(value)
                f.write(",".join(values) + "\n")
        print(f"Saved trial history to {filename}")

    def closeEvent(self, event):
        for player in self.players:
            player.stop()
        self.ding_sound.stop()
        self.buzz_sound.stop()
        self.frame_timer.stop()
        print("Saving...")
        self.save_trial_history_to_csv()
        event.accept()
        QApplication.quit()
        _exit(0) # must be present to close completely


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = SimpleReactionTest(VIDEO_FILES)
    sys.exit(app.exec_())
