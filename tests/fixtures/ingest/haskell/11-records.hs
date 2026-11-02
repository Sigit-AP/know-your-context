-- Record syntax with update.
module Demo.Records where

data Config = Config
  { configName :: String
  , configLimit :: Int
  , configDebug :: Bool
  }

defaultConfig :: Config
defaultConfig = Config "demo" 100 False

withLimit :: Int -> Config -> Config
withLimit n c = c { configLimit = n }

describe :: Config -> String
describe = configName
