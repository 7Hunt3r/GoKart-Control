; GoKart Control: regenerative brake on request from the kart's Heltec.
;
; The Heltec sends COMM_CUSTOM_APP_DATA "GKB" <amps> <tenths-of-seconds> when the pit
; presses STOP (at the same moment it ramps the drive current limit to zero). This script
; takes the output away from the pedal app for that long, applies the brake current, then
; releases. Without this script the STOP request is ignored and the kart coasts.
;
; REQUIRES: Motor Settings > General > Current > "Motor Current Max Brake" set non-zero
; (e.g. -40 A) on this controller; the brake current is clamped to that limit. The battery
; regen limit (-100 A in the baseline capture) also applies. Saved-config change: owner's call.
;
; Load with VESC Tool > VESC Dev Tools > LispBM, "Upload code" then "Run on boot".
; Firmware 6.00. Test with the drive wheels off the ground first.

(def brake-amps 40.0)
(def brake-secs 2.5)
(def brake-max-secs 6.0)

(defun do-brake (amps secs) {
    (var s (if (> secs brake-max-secs) brake-max-secs secs))
    (app-disable-output (to-i (* s 1000)))   ; pedal app has no say while we brake
    (var t0 (systime))
    (loopwhile (< (secs-since t0) s) {
        (set-brake amps)
        (sleep 0.05)
    })
    (set-brake 0)
})

(event-enable 'event-data-rx)

(defun handler ()
    (loopwhile t
        (recv
            ((event-data-rx . (? data))
                (if (and (>= (buflen data) 5)
                         (= (bufget-u8 data 0) 71)      ; G
                         (= (bufget-u8 data 1) 75)      ; K
                         (= (bufget-u8 data 2) 66))     ; B
                    (do-brake (to-float (bufget-u8 data 3))
                              (/ (to-float (bufget-u8 data 4)) 10.0))))
            (_ nil))))

(spawn handler)
