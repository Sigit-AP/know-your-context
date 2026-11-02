;;; Basic defun forms with optional and rest args.
(defun demo-add (a b)
  "Add A and B."
  (+ a b))

(defun demo-greet (name &optional greeting)
  "Greet NAME with GREETING."
  (format "%s, %s" (or greeting "hello") name))

(defun demo-variadic (first &rest rest)
  "Return FIRST and the count of REST."
  (cons first (length rest)))

(defun demo-clamp (value low high)
  "Clamp VALUE between LOW and HIGH."
  (min (max value low) high))
