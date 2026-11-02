require "json"
require "./util"

class Greeter
  def greet(name : String)
    helper(name)
  end

  def helper(x : String)
    puts x
  end
end

module Util
  def self.run
    greet
  end
end
