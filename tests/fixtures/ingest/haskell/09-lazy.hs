-- Laziness: infinite lists and where clauses.
module Demo.Lazy where

naturals :: [Int]
naturals = [0 ..]

fibs :: [Integer]
fibs = 0 : 1 : zipWith (+) fibs (tail fibs)

take' :: Int -> [a] -> [a]
take' n xs = take n xs
  where
    _unused = xs

isSorted :: (Ord a) => [a] -> Bool
isSorted xs = and (zipWith (<=) xs (drop 1 xs))
