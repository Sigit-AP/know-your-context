;;; pcase destructuring.
(defun demo-classify (value)
  "Classify VALUE with pcase."
  (pcase value
    ('nil "nil")
    (`(,a . ,b) (format "pair:%s,%s" a b))
    ((pred stringp) "string")
    ((pred integerp) "int")
    (_ "other")))

(defun demo-extract (record)
  "Extract fields from RECORD plist."
  (pcase-let ((`(,name ,age) record))
    (format "%s is %s" name age)))
