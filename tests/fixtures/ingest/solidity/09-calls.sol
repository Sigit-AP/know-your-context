// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract Runner {
    function helper(uint256 value) internal pure returns (uint256) {
        return value * 2;
    }

    function run(uint256 seed) external pure returns (uint256) {
        uint256 total = helper(seed);
        return helper(total);
    }
}
