# Alarm System Development Guide - Memory Constraints

Development guide for building alarm features on memory-constrained Echo Show 5 kiosks. For device specs and general optimization, see [ECHO_DEVICE_SETUP.md](ECHO_DEVICE_SETUP.md).

## Critical Constraints

- **Available RAM**: ~250-350 MB under normal operation
- **Critical Threshold**: < 200 MB MemAvailable triggers OOM crashes
- **WebView typical**: 150-200 MB, can balloon to 500+ MB under stress

### Memory Management Architecture

#### Clock Component Memory Profile

**Location**: `frontend-kiosk/src/components/Clock.jsx`

**Memory Usage:**
- Preloads 30 photos into browser memory
- ~24 MB total (decoded bitmap data, ~0.8 MB per photo)
- Uses `usePreloadedPhotos` hook from `frontend-kiosk/src/hooks/usePreloadedPhotos.js`
- Photos stored as: `Map<filename, {url: blob URL, image: Image object}>`
- Blob URLs created via `URL.createObjectURL()`

**Why This Matters:**
- Clock runs 24/7 showing slideshow + weather
- Photos must stay preloaded for smooth 30-second transitions
- Cannot interrupt this for alarms without freeing memory first

#### Existing Memory Optimization Pattern

```jsx
// frontend-kiosk/src/App.jsx
const [showTranscription, setShowTranscription] = useState(false);

// Clock unmounts during voice transcription to free ~24MB
{!showTranscription && <Clock />}

// When transcription closes, Clock remounts and reloads photos
```

This pattern **works** and has been battle-tested. It must be replicated for alarms.

---

## What Went Wrong - Alarm System Failures

### Failure 1: Clock Stayed Mounted During Alarms

**The Problem:**
```jsx
// WRONG - Clock stayed mounted when alarm fired
{!showTranscription && <Clock />}
<AlarmOverlay alarm={activeAlarm} />
```

**What Happened:**
1. Alarm fires, AlarmOverlay renders on top of Clock
2. Clock's 24 MB of photos still in memory
3. AlarmOverlay animations + rendering add 10-20 MB
4. WebView process: 170 MB → 250 MB → 503 MB (memory leak)
5. MemAvailable drops below 100 MB
6. Browser crash loop starts

**Monitoring Output:**
```
WebView: 403 MB → 453 MB → 503 MB
MemAvailable: 254 MB → 139 MB → 41 MB
Result: FATAL EXCEPTION (repeated every 500ms)
```

### Failure 2: Manual Memory Cleanup Hook

**The Problem:**
```jsx
// WRONG - Tried to manually clean up blob URLs
const cleanup = useCallback(() => {
    preloadedImages.forEach((img) => URL.revokeObjectURL(img.url));
    setPreloadedImages(new Map());
}, [preloadedImages]);

// This caused infinite loop because cleanup changed every render
useEffect(() => {
    return () => cleanup();
}, [cleanup]); // ❌ cleanup reference changes constantly
```

**What Happened:**
1. Clock mounts → loads photos
2. `cleanup` function created with current `preloadedImages` Map
3. useEffect sees new `cleanup` reference → runs cleanup
4. Photos cleared → Clock tries to reload
5. New `cleanup` created → useEffect runs again
6. Infinite loop: mount → cleanup → mount → cleanup...
7. Photos never load, user sees black screen

**Lesson Learned:**
Don't fight React's garbage collection. Let unmounting handle cleanup naturally.

### Failure 3: Web Audio API on Low Memory

**The Problem:**
```jsx
// AlarmOverlay tried to create audio beeps
const audioContext = new AudioContext();
const oscillator = audioContext.createOscillator();
// ... 880Hz beep pattern
```

**What Happened:**
- Audio API allocates buffers and processing threads
- On 974 MB device already at 80% memory usage
- AudioContext creation throws or causes additional crashes
- No error handling meant alarm UI never appeared

---

## Requirements for New Alarm Implementation

### Architecture Pattern (MUST FOLLOW)

```jsx
// frontend-kiosk/src/App.jsx
const [activeAlarm, setActiveAlarm] = useState(null);
const [showTranscription, setShowTranscription] = useState(false);

return (
    <div>
        {/* Clock unmounts during alarm OR transcription */}
        {!showTranscription && !activeAlarm && <Clock />}

        {/* Mic button also hidden during alarms */}
        {!showTranscription && !activeAlarm && (
            <MicButton ... />
        )}

        {/* Transcription overlay */}
        {showTranscription && <TranscriptionOverlay ... />}

        {/* Alarm overlay - separate from Clock */}
        <AlarmOverlay
            alarm={activeAlarm}
            onDismiss={(id) => {
                // Send acknowledge to backend
                sendMessage(JSON.stringify({
                    type: "alarm_acknowledge",
                    alarm_id: id
                }));
                setActiveAlarm(null); // Clock will remount
            }}
            onSnooze={(id, minutes) => {
                // Send snooze to backend
                sendMessage(JSON.stringify({
                    type: "alarm_snooze",
                    alarm_id: id,
                    snooze_minutes: minutes
                }));
                setActiveAlarm(null); // Clock will remount
            }}
        />
    </div>
);
```

### AlarmOverlay Component Requirements

**MUST BE:**
1. **Lightweight** - Target < 5 MB memory footprint
2. **CSS animations only** - No canvas, no video, no heavy graphics
3. **Defensive rendering** - Wrap entire render in try-catch with fallback UI
4. **No Web Audio API** - Or wrap in try-catch with silent fallback
5. **Simple DOM structure** - Avoid complex SVG or gradient animations

**Example Minimal Implementation:**
```jsx
export default function AlarmOverlay({ alarm, onDismiss, onSnooze }) {
    if (!alarm) return null;

    try {
        return (
            <div className="fixed inset-0 z-[100] bg-gradient-to-br from-gray-900 to-black">
                <div className="flex items-center justify-center h-full">
                    <div className="text-center">
                        <h1 className="text-6xl font-bold text-white mb-4">
                            {alarm.label || 'ALARM'}
                        </h1>
                        <div className="text-3xl text-white/70 mb-8">
                            {formatTime(alarm.alarm_time)}
                        </div>
                        <div className="flex gap-4">
                            <button
                                onClick={() => onSnooze(alarm.alarm_id, 5)}
                                className="px-8 py-4 bg-blue-500 text-white rounded-lg text-xl"
                            >
                                Snooze 5min
                            </button>
                            <button
                                onClick={() => onDismiss(alarm.alarm_id)}
                                className="px-8 py-4 bg-red-500 text-white rounded-lg text-xl"
                            >
                                Dismiss
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        );
    } catch (error) {
        console.error('AlarmOverlay error:', error);
        // Fallback - ALWAYS provide dismiss button
        return (
            <div className="fixed inset-0 z-[100] bg-black flex items-center justify-center">
                <button
                    onClick={() => onDismiss(alarm.alarm_id)}
                    className="px-12 py-6 bg-red-600 text-white text-2xl rounded"
                >
                    DISMISS ALARM
                </button>
            </div>
        );
    }
}
```

---

## Backend Integration

Backend alarm system is already implemented. Key integration points:

**WebSocket messages:**

**Alarm Fires:**
```json
{
    "type": "alarm_trigger",
    "alarm_id": "uuid-here",
    "label": "Wake up",
    "alarm_time": "2026-01-22T07:30:00-05:00"
}
```

**Dismiss Alarm:**
```json
{
    "type": "alarm_acknowledge",
    "alarm_id": "uuid-here"
}
```

**Snooze Alarm:**
```json
{
    "type": "alarm_snooze",
    "alarm_id": "uuid-here",
    "snooze_minutes": 5
}
```

---

## Testing & Debugging

**Memory monitoring** (run before testing alarms):
```bash
./scripts/monitor_alarm_crash.sh  # Real-time crash tracking
./scripts/echo/check_echo_memory.sh     # Quick snapshot
```

**Alarm management:**
```bash
uv run python scripts/clear_all_alarms.py  # Clear stuck alarms
sqlite3 data/alarms.db "SELECT * FROM alarms;"  # View database
```

For detailed device debugging, see [ECHO_DEVICE_SETUP.md](ECHO_DEVICE_SETUP.md#troubleshooting).

---

## Development Checklist

### Before You Code

- [ ] Read this document fully
- [ ] Understand why Clock must unmount during alarms
- [ ] Review existing transcription overlay pattern in App.jsx
- [ ] Connect Echo Show via USB for monitoring

### During Development

- [ ] Start memory monitor: `./scripts/monitor_alarm_crash.sh`
- [ ] Test with actual device, not just browser
- [ ] Check MemAvailable stays above 150 MB during alarm
- [ ] Verify WebView process doesn't exceed 250 MB
- [ ] Test dismiss/snooze buttons work even if rendering fails

### Testing Scenarios

1. **Normal alarm flow:**
   - Set alarm for 1 minute from now
   - Watch memory monitor
   - Verify Clock unmounts when alarm fires
   - Verify photos reload after dismiss

2. **Memory stress test:**
   - Set alarm
   - Open transcription overlay (Clock unmounts)
   - Let alarm fire while overlay open
   - Verify no crash

3. **Crash recovery:**
   - Force WebView crash: `adb shell am crash de.ozerov.fully`
   - Verify app recovers
   - Verify alarm still fires after recovery

4. **Multiple alarms:**
   - Set 3 alarms 1 minute apart
   - Snooze first, dismiss second, let third re-fire
   - Verify no memory accumulation

---

## Key Insights & Lessons

### What Works

1. **Unmounting pattern** - React's natural cleanup via unmounting is reliable
2. **WebSocket protocol** - Backend alarm system is solid, no issues there
3. **Transcription overlay** - Already unmounts Clock successfully, copy this pattern
4. **Simple UI** - Minimal DOM + CSS animations work fine on Echo Show

### What Doesn't Work

1. **Manual cleanup hooks** - Don't try to manage blob URL lifecycle manually
2. **Keeping Clock mounted** - 24 MB photo memory causes cascading failures
3. **Web Audio API** - Too fragile on low-memory devices
4. **Complex animations** - Canvas, heavy gradients, particles = memory pressure
5. **Fighting React** - Let it manage lifecycle, don't override with refs/effects

### Golden Rule

**When alarm fires, Clock MUST unmount. Period.**

Everything else is optional (audio, fancy animations, etc). But Clock unmounting is non-negotiable for device stability.

---

## References

- [ECHO_DEVICE_SETUP.md](ECHO_DEVICE_SETUP.md) - Device setup and optimization
- Backend: `src/backend/services/alarm_scheduler.py`, `src/backend/routers/alarms.py`
- Frontend: `frontend-kiosk/src/hooks/usePreloadedPhotos.js`, `TranscriptionOverlay.jsx`
