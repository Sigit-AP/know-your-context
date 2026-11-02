-- Fold/scan library-style definitions.
module Demo.Folds where

sum' :: (Num a) => [a] -> a
sum' = foldr (+) 0

reverse' :: [a] -> [a]
reverse' = foldl (flip (:)) []

scanSum :: (Num a) => [a] -> [a]
scanSum = scanl (+) 0
