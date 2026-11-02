;;; cl-defstruct with constructor and accessors.
(require 'cl-lib)

(cl-defstruct demo-point
  "A 2D point."
  (x 0 :type integer)
  (y 0 :type integer))

(cl-defstruct (demo-size (:constructor demo-size-create))
  "A 2D size."
  width
  height)

(defun demo-point-origin-p (p)
  "Non-nil when P is at the origin."
  (and (zerop (demo-point-x p))
       (zerop (demo-point-y p))))
