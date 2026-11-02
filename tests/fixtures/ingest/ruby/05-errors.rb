# begin/rescue/ensure and custom exceptions.
class ConfigError < StandardError
  def initialize(key)
    super("missing config key: #{key}")
    @key = key
  end
end

def read_config(mapping, key)
  mapping.fetch(key)
rescue KeyError
  raise ConfigError, key
end

def read_safe(mapping, key)
  read_config(mapping, key)
rescue ConfigError
  ""
ensure
  mapping.freeze
end
