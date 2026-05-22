import cv2  # type: ignore
import numpy as np  # type: ignore
import threading
import argparse
import time
import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageTk  # type: ignore
import matplotlib.pyplot as plt  # type: ignore

class BoxTracker:
    """
    Advanced tracking algorithm using Exponential Moving Average (EMA) 
    to completely eliminate box flickering and smooth out movements.
    """
    def __init__(self, alpha=0.3, grace_frames=5):
        self.alpha = alpha
        self.grace_frames = grace_frames
        self.tracked = [] # Stores dicts: {'box': (x,y,w,h), 'missed': 0}
        
    def update(self, new_boxes):
        updated = []
        # Try to match new camera detections to existing stabilized tracks
        for nx, ny, nw, nh in new_boxes:
            matched = False
            ncx, ncy = nx + nw//2, ny + nh//2
            
            for i, track in enumerate(self.tracked):
                ox, oy, ow, oh = track['box']
                ocx, ocy = ox + ow//2, oy + oh//2
                
                # If the new box is within distance of an old box, smooth it!
                dist = ((ncx - ocx)**2 + (ncy - ocy)**2)**0.5
                if dist < max(ow, oh): 
                    sm_box = (
                        int(nx * self.alpha + ox * (1 - self.alpha)),
                        int(ny * self.alpha + oy * (1 - self.alpha)),
                        int(nw * self.alpha + ow * (1 - self.alpha)),
                        int(nh * self.alpha + oh * (1 - self.alpha))
                    )
                    updated.append({'box': sm_box, 'missed': 0})
                    self.tracked.pop(i)
                    matched = True
                    break
            
            if not matched:
                updated.append({'box': (nx, ny, nw, nh), 'missed': 0})
                
        # Keep old boxes alive briefly if the camera misses a frame (anti-flicker)
        for track in self.tracked:
            track['missed'] += 1
            if track['missed'] < self.grace_frames:
                updated.append(track)
                
        self.tracked = updated
        return [t['box'] for t in self.tracked]

def draw_cyber_rect(img, bbox, color=(0, 255, 0)):
    x, y, w, h = bbox
    cv2.rectangle(img, (x, y), (x+w, y+h), color, 1)
    
    cx, cy = x + w//2, y + h//2
    cv2.line(img, (cx - 15, cy), (cx + 15, cy), color, 1)
    cv2.line(img, (cx, cy - 15), (cx, cy + 15), color, 1)
    cv2.circle(img, (cx, cy), 5, color, 1)

def pixelate_face(image, blocks=12):
    h, w = image.shape[:2]
    x_steps = np.linspace(0, w, blocks + 1, dtype="int")
    y_steps = np.linspace(0, h, blocks + 1, dtype="int")

    for i in range(1, len(y_steps)):
        for j in range(1, len(x_steps)):
            startX, startY = x_steps[j - 1], y_steps[i - 1]  # type: ignore
            endX, endY = x_steps[j], y_steps[i]  # type: ignore

            roi = image[startY:endY, startX:endX]  # type: ignore
            if roi.size > 0:
                (B, G, R) = [int(x) for x in cv2.mean(roi)[:3]]
                cv2.rectangle(image, (startX, startY), (endX, endY), (B, G, R), -1)
    return image

class VideoCaptureThread:
    """
    Background thread to continuously grab frames from the camera.
    This significantly improves FPS by separating I/O from processing.
    """
    def __init__(self, src=0):
        self.cap = cv2.VideoCapture(src)
        self.ret, self.frame = self.cap.read()
        self.running = True
        self.thread = threading.Thread(target=self.update, args=())
        self.thread.daemon = True
        self.thread.start()

    def update(self):
        while self.running:
            if self.cap.isOpened():
                self.ret, self.frame = self.cap.read()
            time.sleep(0.01)

    def read(self):
        return self.ret, self.frame

    def stop(self):
        self.running = False
        self.thread.join()
        if self.cap.isOpened():
            self.cap.release()

class SecurityApp:
    def __init__(self, window, window_title, args):
        self.window = window
        self.window.title(window_title)
        self.window.protocol("WM_DELETE_WINDOW", self.on_closing)
        
        self.args = args
        self.video_source = args.source
        
        # Start background capture
        self.vid = VideoCaptureThread(self.video_source)
        
        # Style UI
        style = ttk.Style()
        style.configure("TButton", font=("Helvetica", 12))
        style.configure("TLabel", font=("Helvetica", 14))

        self.main_frame = ttk.Frame(window, padding="15")
        self.main_frame.grid(row=0, column=0, sticky=tk.W+tk.E+tk.N+tk.S)

        # Video Canvas
        self.canvas = tk.Canvas(self.main_frame, width=640, height=480, bg='black', highlightthickness=0)
        self.canvas.grid(row=0, column=0, columnspan=2, pady=10)

        # Status Display
        self.status_label = ttk.Label(self.main_frame, text="Initializing System...", font=("Consolas", 14, "bold"))
        self.status_label.grid(row=1, column=0, columnspan=2, pady=10)

        # Controls
        self.btn_privacy = ttk.Button(self.main_frame, text="🛡️ Toggle Privacy Mode", command=self.toggle_privacy)
        self.btn_privacy.grid(row=2, column=0, pady=5, sticky=tk.W)

        self.btn_quit = ttk.Button(self.main_frame, text="⛔ Exit & View Analytics", command=self.on_closing)
        self.btn_quit.grid(row=2, column=1, pady=5, sticky=tk.E)

        # Setup computer vision
        self.face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
        self.smile_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_smile.xml')
        self.tracker = BoxTracker(alpha=0.3, grace_frames=4)
        
        # Parse Configuration
        self.privacy_mode = args.privacy
        self.proximity_threshold = args.prox
        self.photo = None
        
        # Analytics Tracking state
        self.time_history = []
        self.count_history = []
        self.start_time = time.time()
        
        self.delay = 15 # Refresh delay in ms
        self.update()

    def toggle_privacy(self):
        self.privacy_mode = not self.privacy_mode

    def update(self):
        ret, frame = self.vid.read()
        if ret and frame is not None:
            # Mirror the frame
            frame = cv2.flip(frame, 1)
            frame_resized = cv2.resize(frame, (640, 480))
            gray = cv2.cvtColor(frame_resized, cv2.COLOR_BGR2GRAY)
            
            # 1. Target Acquisition
            raw_faces = self.face_cascade.detectMultiScale(gray, scaleFactor=1.2, minNeighbors=6, minSize=(60, 60))
            
            # 2. Tracking & Smoothing
            stable_faces = self.tracker.update(raw_faces)
            face_count = len(stable_faces)
            
            # Record Data for Analytics
            current_time = time.time() - self.start_time
            if len(self.time_history) == 0 or current_time - self.time_history[-1] >= 1.0:
                # Store roughly 1 data point per second
                self.time_history.append(current_time)
                self.count_history.append(face_count)
            
            proximity_alert = False

            # 3. Analyze bounds and draw
            for (x, y, w, h) in stable_faces:
                face_area = w * h
                if face_area > self.proximity_threshold:
                    proximity_alert = True
                    box_color = (0, 0, 255) # Red
                else:
                    box_color = (0, 255, 0) # Green

                if self.privacy_mode:
                    face_roi = frame_resized[y:y+h, x:x+w]
                    # ensure valid ROI before pixelating
                    if face_roi.shape[0] > 0 and face_roi.shape[1] > 0:
                        frame_resized[y:y+h, x:x+w] = pixelate_face(face_roi, blocks=10)
                    box_color = (150, 150, 150) # Gray
                    
                draw_cyber_rect(frame_resized, (x, y, w, h), color=box_color)

                # Micro-Expression (Smile) Tracking
                if not self.privacy_mode:
                    lower_face_gray = gray[y + h//2 : y + h, x : x + w]
                    mood = "Neutral"
                    if lower_face_gray.shape[0] > 20 and lower_face_gray.shape[1] > 20:
                        smiles = self.smile_cascade.detectMultiScale(
                            lower_face_gray, scaleFactor=1.5, minNeighbors=12, minSize=(25, 25)
                        )
                        if len(smiles) > 0:
                            mood = "Smiling"
                    
                    cv2.putText(frame_resized, f"STATUS: {mood}", (x, y - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, box_color, 2)

            # Update Labels
            mode_text = "[ANONYMIZATION: ACTIVE]" if self.privacy_mode else "[ANONYMIZATION: INACTIVE]"
            self.status_label.config(text=f"{mode_text}   |   TARGETS ACQUIRED: {face_count}")

            if proximity_alert:
                cv2.putText(frame_resized, 'WARNING: SUBJECT TOO CLOSE', (20, 120), cv2.FONT_HERSHEY_TRIPLEX, 0.9, (0, 0, 255), 2)
                cv2.rectangle(frame_resized, (0, 0), (frame_resized.shape[1], frame_resized.shape[0]), (0, 0, 255), 10)

            # 4. Render back to GUI
            img_rgb = cv2.cvtColor(frame_resized, cv2.COLOR_BGR2RGB)
            self.photo = ImageTk.PhotoImage(image=Image.fromarray(img_rgb))
            self.canvas.create_image(0, 0, image=self.photo, anchor=tk.NW)

        # Loop
        self.window.after(self.delay, self.update)

    def on_closing(self):
        """Cleanup resources and show analytics before full exit."""
        self.vid.stop()
        self.window.destroy()
        self.show_analytics()

    def show_analytics(self):
        """Displays a Matplotlib chart showing target history."""
        if not self.count_history:
            print("No data recorded.")
            return
            
        plt.style.use('dark_background')
        plt.figure(figsize=(10, 5))
        plt.plot(self.time_history, self.count_history, color='cyan', linewidth=2, marker='o', markersize=4)
        plt.fill_between(self.time_history, self.count_history, color='cyan', alpha=0.3)
        plt.title('Post-Session Analytics: Targets Over Time', fontsize=14, pad=15)
        plt.xlabel('Session Time (seconds)', fontsize=12)
        plt.ylabel('Number of Targets Detected', fontsize=12)
        
        max_targets = max(self.count_history) if self.count_history else 0
        plt.yticks(range(0, max_targets + 2))
        plt.grid(True, alpha=0.2, linestyle='--')
        plt.tight_layout()
        plt.show()

def parse_args():
    parser = argparse.ArgumentParser(description="Advanced Security Vision System")
    parser.add_argument("--source", type=int, default=0, help="Camera source (default: 0)")
    parser.add_argument("--prox", type=int, default=55000, help="Proximity area threshold (default: 55000)")
    parser.add_argument("--privacy", action="store_true", help="Start with privacy mode enabled")
    return parser.parse_args()

def main():
    args = parse_args()
    root = tk.Tk()
    app = SecurityApp(root, "Security Analytics System", args)
    root.mainloop()

if __name__ == "__main__":
    main()
