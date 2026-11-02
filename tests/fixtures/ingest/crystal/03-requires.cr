# require forms: stdlib, local and glob-ish.
require "json"
require "http/client"
require "./local_helper"
require "./sub/*"

def boot : String
  JSON.build { }.to_s
end
