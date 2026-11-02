# require and require_relative forms.
require "json"
require "net/http"
require_relative "local_helper"
require_relative "sub/util"

def boot
  JSON.generate({})
end
