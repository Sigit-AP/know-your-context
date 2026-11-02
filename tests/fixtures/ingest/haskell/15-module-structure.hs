-- Module with export list and re-export.
module Demo.Structure
  ( limit
  , defaultName
  , describe
  , module Demo.Calls
  ) where

import Demo.Calls (helper, run)

limit :: Int
limit = 100

defaultName :: String
defaultName = "demo"

describe :: String
describe = defaultName
