// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./Token.sol";
import "./lib/Math.sol";
import {IERC20} from "./interfaces/IERC20.sol";
import * as Utils from "./Utils.sol";

contract Importer {
    function boot() public pure returns (bool) {
        return true;
    }
}
