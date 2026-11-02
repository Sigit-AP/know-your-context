# Singleton methods and eigenclasses.
class Config
  def self.load(path)
    new(path)
  end

  class << self
    def default
      new("defaults.yml")
    end
  end

  def initialize(path)
    @path = path
  end
end
