-- do-notation, bind chains and Maybe monad.
module Demo.Monads where

safeDiv :: Double -> Double -> Maybe Double
safeDiv _ 0 = Nothing
safeDiv x y = Just (x / y)

pipeline :: Double -> Maybe Double
pipeline x = do
  a <- safeDiv x 2
  b <- safeDiv a 3
  safeDiv b 4

chained :: Maybe Int -> Maybe Int
chained mx = mx >>= \x -> Just (x + 1) >>= \y -> Just (y * 2)
