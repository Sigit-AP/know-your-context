# begin/rescue/ensure and custom exceptions.
class ConfigError < Exception
  getter key : String

  def initialize(@key : String)
    super("missing config key: #{key}")
  end
end

def read_config(map : Hash(String, String), key : String) : String
  map.fetch(key) { raise ConfigError.new(key) }
end

def read_safe(map : Hash(String, String), key : String) : String?
  read_config(map, key)
rescue ConfigError
  nil
end
