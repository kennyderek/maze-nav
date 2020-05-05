
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.distributions import Categorical
from torch.autograd import Variable
import torch.optim as optim

import numpy as np
from sim import MazeSimulator, ShortCorridor

import matplotlib.pyplot as plt
from tqdm import tqdm
from random import random

from utils import *

import torch.multiprocessing as mp
import copy
from collections import OrderedDict


class A2C(nn.Module):

    def __init__(self, state_input_size, action_space_size, seed, lr, lr_critic, use_opt=False, ppo=False):
        super(A2C, self).__init__()

        torch.manual_seed(seed)

        self.state_input_size = state_input_size
        self.action_space_size = action_space_size

        print ("\nstate_input_size: ", self.state_input_size)
        print ("\naction_space_size: ", self.action_space_size)

        self.critic = Critic(state_input_size, 1)

        self.policy = Actor(state_input_size, action_space_size)

        # self.policy = Recurrent_Actor(state_input_size, action_space_size)
        self.optimizer = optim.Adam(self.critic.parameters(), lr=lr_critic)

        self.lr = lr
        self.lr_critic = lr_critic

        self.ppo = ppo

        self.use_opt = use_opt
        if use_opt:
            self.init_optimizers()
        else:
            for param in self.policy.parameters():
                param.requires_grad = True
            # for param in self.critic.parameters():
            #     param.requires_grad = True
            self.opt_c = optim.Adam(self.critic.parameters(), lr=self.lr_critic)

        self.old_policy = Actor(state_input_size, action_space_size)

    def init_optimizers(self):
        self.opt_a = optim.Adam(self.policy.parameters(), lr=self.lr)
        self.opt_c = optim.Adam(self.critic.parameters(), lr=self.lr_critic)

    def compute_loss(self, state, action, weights):
        '''
        weights is what to multiply the log probability by
        '''
        if self.ppo:
            logp_ratios =  Categorical(self.policy(state)).log_prob(action) - Categorical(self.old_policy(state)).log_prob(action)
            ratios = torch.exp(logp_ratios)
            clipped_adv = torch.clamp(ratios, 1 - 0.02, 1 + 0.02) * weights
            non_clipped_adv = ratios * weights
            # theoretically, big = good too keep this from converging too quickly,
            # so we encourage by adding to the thing we are trying to maximize
            # entropy_loss = Categorical(self.policy(state)).entropy()
            return -(torch.min(clipped_adv, non_clipped_adv)).mean(), None
        else:
            ratios =  Categorical(self.policy(state)).log_prob(action)
            # entropy =  Categorical(self.policy(state)).log_prob(action)
            loss = ratios * weights
            # print("policy loss:", loss.mean())
            return -(loss.mean()), None

    def compute_critic_loss(self, state, weights):
        '''
        weights is what to multiply the log probability by
        '''
        loss = -self.critic(state)*weights
        # print("critic loss:", loss.mean())
        return loss.mean()
    
    def normalize_advantages(self, advantages):
        '''
        instead scale advantages between 0 and 1?
        '''
        advantages = np.array(advantages)
        std = np.std(advantages)
        mean = advantages.mean()
        if std != 0:
            advantages = (advantages - mean) / std  
        return advantages

    def update_params(self, params, loss, step_size=0.1):
        """
        Apply one step of gradient descent on the loss function `loss`, with 
        step-size `step_size`, and returns the updated parameters of the neural 
        network.
        """
        # params = OrderedDict(self.policy.named_parameters())

        grads = torch.autograd.grad(loss, params.values(),
                                    create_graph=False)

        for (name, param), grad in zip(params.items(), grads):
            params[name] = param - step_size * grad

        # self.policy.load_state_dict(params)
        return params


    def __step(self, env, horizon):
        '''
        makes horizon steps in this trajectory in this environment
        '''
        lam = 0.9

        # number of steps to take in this environment
        if self.ppo:
            S, A, R, episode = generate_episode(self.old_policy, env, horizon)
        else:
            S, A, R, episode = generate_episode(self.policy, env, horizon)
        Rn = self.normalize_advantages(R)

        traj_len = len(A)

        # discounted_rewards = [0] * traj_len
        # reward_tplusone = 0
        # compute discounted rewards
        # for t in reversed(list(range(traj_len))):
        #     discounted_rewards[t] = R[t] + lam * reward_tplusone
        #     reward_tplusone = discounted_rewards[t]

        # compute advantage (of that action)
        td_err = [0]*traj_len
        adv = [0]*traj_len
        for t in range(traj_len):
            '''
            The commented out method may/may not be better, all it does is help propagate reward
            farther up the chain
            '''
            # k = traj_len-t
            # if S[t+k] == None:
            #     advantages[t] = sum([R[t+i]*lam**(i) for i in range(0, k)]) - self.critic(S[t])
            # else:
            #     advantages[t] = sum([R[t+i]*lam**(i) for i in range(k)]) + lam**k*self.critic(S[t+k]) - self.critic(S[t])
            if S[t+1] == None:
                td_err[t] = Rn[t] - self.critic(S[t]).item()
                adv[t] = Rn[t] - self.critic(S[t]).item()
            else:
                td_err[t] = Rn[t] + (lam*self.critic(S[t+1]) - self.critic(S[t])).item()
                adv[t] = Rn[t] - self.critic(S[t]).item()
        
        mini_batch_states = []
        mini_batch_actions = []
        mini_batch_td = []
        mini_batch_adv = []
        mini_batch_rewards = []
        for t in range(traj_len):
            mini_batch_states.append(S[t].data.numpy())
            mini_batch_actions.append(A[t])
            mini_batch_td.append(td_err[t])
            mini_batch_adv.append(adv[t])
            mini_batch_rewards.append(R[t])

        return env, mini_batch_states, mini_batch_actions, mini_batch_td, mini_batch_adv, mini_batch_rewards, episode

    '''
    For 2D Maze nav task:

    MAML models were trained with up to 500 meta-iterations,
        model with the best avg performance was used for train-time

    policy was trained with MAML to maximize performance after 1 policy gradient update using 20 trajectories
        - thus: a batch size of 20 was used (during meta-train time?/and meta-test?)

    In our evaluation, we compare adaptation to a new task with up to 4 gradient updates, each with 40 samples.
    '''
    def train(self, env, num_batches = 1, batch_size = 20, horizon = 100, batch_envs=None):
        '''
        Train using batch_size samples of complete trajectories, num_batches times (so num_batches gradient updates)
        
        A trajectory is defined as a State, Action, Reward secquence of t steps,
            where t = min(the number of steps to reach the goal, horizon)
        '''
        cumulative_rewards = []
        if self.ppo:
            self.old_policy.load_state_dict(copy.deepcopy(self.policy.state_dict()))

        for batch in tqdm(range(num_batches)):

            if batch_envs == None:
                parallel_envs = [env.generate_fresh() for _ in range(batch_size)]
            else:
                assert len(batch_envs) == batch_size, "supplied envs must match "

            batch_states = []
            batch_actions = []
            batch_td = []
            batch_adv = []
            batch_rewards = []

            q = mp.Queue()
            def multi_process(self, env, horizon, q, done):
                env, s, a, td, adv, r, episode = self.__step(env, horizon)
                q.put((s, a, td, adv, r))
                done.wait()

            done = mp.Event()

            num_processes = batch_size
            self.share_memory()

            processes = []
            for rank in range(num_processes):
                p = mp.Process(target=multi_process, args=(self, parallel_envs[rank], horizon, q, done))
                p.start()
                processes.append(p)

            for i in range(num_processes):
                (s, a, td, adv, r) = q.get()
                batch_states.extend(s)
                batch_actions.extend(a)
                batch_td.extend(td)
                batch_adv.extend(adv)
                batch_rewards.extend(r)
            
            done.set()
            for p in processes:
                p.join()

            print ("\nDONE HERE")
            
            # batch_td = self.normalize_advantages(batch_td)
            cumulative_rewards.append(sum(batch_rewards)/batch_size)

            batch_actor_loss, batch_entropy_loss = self.compute_loss(
                                    state=torch.as_tensor(batch_states, dtype=torch.float32),
                                    action=torch.as_tensor(batch_actions, dtype=torch.float32),
                                    weights=torch.as_tensor(batch_adv, dtype=torch.float32))

            print ("\n ACTOR LOSS")
            
            batch_critic_loss = self.compute_critic_loss(
                                    state=torch.as_tensor(batch_states, dtype=torch.float32),
                                    weights=torch.as_tensor(batch_td, dtype=torch.float32))

            if self.ppo:
                # we make a copy of the current policy to use as the "old" policy in the next iteration
                temp_state_dict = copy.deepcopy(self.policy.state_dict())

            # update new policy
            if self.use_opt:
                self.opt_a.zero_grad()
                batch_actor_loss.backward()
                self.opt_a.step()

                self.opt_c.zero_grad()
                batch_critic_loss.backward()
                self.opt_c.step()
            else:
                # call update params manually, without fancy adaptive stuff
                params = OrderedDict(self.policy.named_parameters())
                self.update_params(params, batch_actor_loss, step_size = self.lr)
                # self.policy.reward_episode = batch_rewards
                # self.update_policy()
                # self.policy.load_state_dict(params)

                # params = OrderedDict(self.critic.named_parameters())
                # self.update_params(params, batch_critic_loss, step_size = self.lr_critic)
                # self.critic.load_state_dict(params)
                self.opt_c.zero_grad()
                batch_critic_loss.backward()
                self.opt_c.step()

            if self.ppo:
                # update old policy to the previous new policy
                self.old_policy.load_state_dict(temp_state_dict)

            if batch % 10 == 0:
                print(cumulative_rewards[-1])

        return cumulative_rewards

    #For recurrent policy
    def update_policy(self):
        R = 0
        rewards = []

        #Discount future rewards back to the present using gamma
        for r in reversed(self.policy.reward_episode):
            R = r + self.policy.gamma * R
            rewards.insert(0, R)

        #Scale rewards
        rewards = torch.FloatTensor(rewards)
        
        #Normalize rewards
        rewards = (rewards - rewards.mean()) / (rewards.std()) #+ float(np.info(np.float32).eps))

        #Calculate loss
        policy_history = torch.stack(self.policy.policy_history)
        # print ("HERE: ", policy_history)
        loss = (torch.mul(self.policy_history, rewards).mul(-1), -1)  #TODO

        #Update network weights
        self.optimizer.zero_grad()
        loss.backward()
        self.optimizer.step()

        #Save and initialize episode history counters
        self.policy.loss_history.append(loss.data[0])
        self.policy.reward_history.append(np.sum(policy.reward_episode))
        self.policy.policy_history.append(self.policy.named_parameters())
        self.policy.reset_episode()

