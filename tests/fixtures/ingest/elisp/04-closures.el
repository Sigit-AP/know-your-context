;;; Lexical closures and flet/cl-flet.
;;; -*- lexical-binding: t -*-

(defun demo-make-counter (start)
  "Return a counter closure starting at START."
  (let ((count start))
    (lambda (&optional amount)
      (setq count (+ count (or amount 1)))
      count)))

(defun demo-apply-twice (fn x)
  "Apply FN to X twice."
  (funcall fn (funcall fn x)))

(defun demo-compose (f g)
  "Compose F after G."
  (lambda (x) (funcall f (funcall g x))))
