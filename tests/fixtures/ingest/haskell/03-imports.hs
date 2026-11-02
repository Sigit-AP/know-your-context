-- Import forms: plain, qualified, selective and hiding.
module Demo.Imports where

import Data.List
import qualified Data.Map as Map
import Data.Maybe (fromMaybe, isJust)
import Data.Char hiding (toLower)
import "containers" Data.Set (Set)

useThem :: [Int] -> Int
useThem xs = length (sort xs)
